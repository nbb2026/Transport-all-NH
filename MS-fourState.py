"""MS-fourState: write the magnetic states D, A, B and E in the PPMS and read
each one at zero field with the SR830 first harmonic.

One step of the sequence, with the state's angle, write field and pulse:

  1. 6221 off; the field is brought to 0 Oe if it is not there already.
  2. The PPMS rotator turns to the state's angle, only at zero field.
  3. The write field is set (Driven) and allowed to settle.
  4. The 6221 sends the write pulse(s): 0 -> I for the pulse width.
  5. The field returns to 0 Oe (Driven).
  6. Read: the 6221 sources a sine of I_read (RMS) whose phase marker is the
     SR830's external reference, as on the PPMS page; the SR830 first
     harmonic X, Y and theta are averaged over the read time.
  7. 6221 off; the point is plotted and written to the CSV.

Stop, an error or the end of the run turns the 6221 off and, if the field is
not at zero, sets 0 Oe (Driven).  The rotator stays where it is: it has no
software stop, so keep MultiVu's Motion Control at hand.

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

APP_TITLE = "MS-fourState"
DATA_DIRECTORY = Path(__file__).resolve().parent / "MS-fourState data"
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
PULSE_DONE_MARGIN_S = 0.2
READ_SAMPLE_INTERVAL_S = 0.1
READ_OVERLOAD_RETRIES = 3
AUTORANGE_MAX_CHANGES = 24
AUTORANGE_LOW_FRACTION = 0.10
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
    repeats: int
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
    def steps(self):
        return tuple((cycle, name) for cycle in range(1, self.repeats + 1) for name in self.sequence)


def parse_sequence(text, names):
    """'D A B E', 'd,a,b,e' or "D A' B' E" -> ('D', 'A', 'B', 'E')."""
    lookup = {name.upper(): name for name in names}
    sequence = []
    for token in str(text).replace(",", " ").replace(";", " ").split():
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


def build_settings(states, sequence_text, values, autorange=True):
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
    if read_rms_a <= 0 or math.sqrt(2.0) * read_rms_a > core.K6221_MAX_ABS_CURRENT_A:
        raise ValueError("Read current must be above 0 and its peak within the 6221 range")
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
    return Settings(
        states=parsed, sequence=sequence,
        repeats=_whole(values["repeats"], "Repeats", 1, 1000),
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


def source_query(source, command):
    with source.lock:
        if source.instrument is None:
            raise RuntimeError("Keithley 6221 is not connected")
        return float(source.instrument.query(command).strip())


class FourStateRun:
    """The measurement itself, without Tk: runs in a worker thread."""

    def __init__(self, ppms, source, lockin, settings, *, emit, stop, motor=None, clock=time.monotonic):
        self.ppms = ppms
        self.source = source
        self.lockin = lockin
        self.settings = settings
        self.emit = emit
        self.stop = stop
        self.motor = motor if motor is not None else core.PPMSMotorDebug(ppms.active)
        self.clock = clock

    # -- helpers ---------------------------------------------------------
    def check(self):
        if self.stop.is_set():
            raise core.PPMSScanCancelled("Stopped")

    def wait(self, seconds):
        if self.stop.wait(max(0.0, float(seconds))):
            raise core.PPMSScanCancelled("Stopped")

    def source_off(self):
        self.source.output_off()

    def lockin_settle_s(self):
        code = int(sr830_query(self.lockin, "OFLT?", 1)[0])
        if not 0 <= code <= 19:
            raise RuntimeError("SR830 time-constant readback invalid")
        tau = (1 if code % 2 == 0 else 3) * 10 ** (code // 2 - 5)
        return max(0.1, 5.0 * tau)

    # -- field and angle ------------------------------------------------
    def field_to(self, target):
        """Driven setpoint; returns the reading once settled for the settle time."""
        s = self.settings
        result = self.ppms.set_field(target, s.field_rate_oe_s, s.approach_code, 1)
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
        result = self.motor.move_and_verify(
            angle, self.settings.decel, cancelled=self.stop.is_set,
            allow_large=large, timeout=900.0 if large else 90.0)
        return result["angle_deg"]

    # -- 6221 -----------------------------------------------------------
    def pulse(self, pulse_ma):
        s = self.settings
        if pulse_ma == 0:
            self.source_off()
            return {"in_compliance": False}
        info = self.source.set_dc_pulse(pulse_ma / 1000.0, s.pulse_width_s, s.compliance_v, s.pulse_count)
        self.wait(float(info.get("total_time_s", 0.0)) + PULSE_DONE_MARGIN_S)
        state = self.source.read_output_state()
        self.source_off()
        if state["output_on"]:
            raise RuntimeError("6221 output was still on after the pulse train; it has been turned off")
        return info

    def start_read_current(self):
        """The PPMS page's sine: phase marker on, as the SR830 reference."""
        s = self.settings
        peak = math.sqrt(2.0) * s.read_current_rms_a
        source = self.source
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
                    ("amplitude", "SOUR:WAVE:AMPL?", peak, 5e-4, 1e-12)):
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
        """First harmonic, external reference from the 6221 marker; filters off."""
        lockin = self.lockin
        for command in ("ILIN 0", "SYNC 0", "HARM 1", "FMOD 0", f"RSLP {core.PPMS_SR830_REFERENCE_SLOPE}"):
            sr830_write(lockin, command)
        readback = {query: int(round(sr830_query(lockin, query, 1)[0])) for query in ("HARM?", "FMOD?", "RSLP?")}
        if readback != {"HARM?": 1, "FMOD?": 0, "RSLP?": core.PPMS_SR830_REFERENCE_SLOPE}:
            raise RuntimeError(f"SR830 setup readback mismatch: {readback}")

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
        """Up on overload, down below 10 % of full scale; returns the code."""
        scales = core.NONLOCAL_SR830_FULL_SCALE_V
        code = int(sr830_query(self.lockin, "SENS?", 1)[0])
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
            elif code > 0 and magnitude < AUTORANGE_LOW_FRACTION * scales[code]:
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
        self.check_read_current()
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
        steps = s.steps
        started = self.clock()
        done = 0
        try:
            self.source_off()
            self.emit("status", "Setting up the SR830: 1st harmonic, external reference")
            self.configure_lockin()
            for index, (cycle, name) in enumerate(steps, 1):
                angle, field, pulse_ma = s.states[name]
                prefix = f"Step {index}/{len(steps)}, state {name}"
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
                    "pulse_in_compliance": bool(pulse.get("in_compliance", False)),
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
        """6221 off; field to 0 Oe if it is not there.  Never moves the rotator."""
        problems = []
        try:
            self.source_off()
        except Exception as error:
            problems.append(f"6221 off failed: {error}")
        try:
            data = self.ppms.read_environment()
            if not core.CombinedControlApp._ppms_field_is_ready(data, 0.0):
                self.ppms.set_field(0.0, self.settings.field_rate_oe_s, self.settings.approach_code, 1)
                self.emit("status", "Run ended: field set to 0 Oe (Driven); 6221 off")
        except Exception as error:
            problems.append(f"setting 0 Oe failed: {error}")
        if problems:
            self.emit("log", "Shutdown: " + "; ".join(problems) + ". Check MultiVu and the 6221.")


def nice_ticks(low, high, count=5):
    if not (math.isfinite(low) and math.isfinite(high)):
        return []
    if high <= low:
        low, high = low - 1.0, high + 1.0
    raw = (high - low) / max(1, count)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    first = math.ceil(low / step) * step
    ticks = []
    value = first
    while value <= high + step * 1e-9:
        ticks.append(0.0 if abs(value) < step * 1e-9 else value)
        value += step
    return ticks


class StatePlot:
    """H, I and SR830 X per step, as panel A of the figure."""

    PANELS = (
        ("H write (T)", lambda row: row["write_field_set_oe"] / OE_PER_T),
        ("I pulse (mA)", lambda row: row["pulse_current_ma"]),
        ("SR830 X (µV)", lambda row: row["x_v"] * 1e6),
    )

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
                low, high = low - pad, high + pad
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
                canvas.create_rectangle(x - 4, y - 4, x + 4, y + 4, fill=color, outline=color)
                if index == 2:
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
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("1360x900")
        root.minsize(1100, 760)
        self.events = queue.Queue()
        self.ppms = core.PPMSRemoteReadbackController(self.log_from_thread)
        self.source = core.Keithley6221Controller(self.log_from_thread)
        self.lockin = core.SafeVisaIdentityController(self.log_from_thread, "SR830", "SR830", owner_scope=APP_TITLE)
        self.stop_event = threading.Event()
        self.worker = None
        self.busy = False
        self.rows = []
        self.csv_file = None
        self.csv_writer = None
        self.run_folder = None
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self._drain)

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
        run.columnconfigure(8, weight=1)
        ttk.Label(run, text="Sequence").grid(row=0, column=0, sticky="w")
        self.sequence_var = tk.StringVar(value=DEFAULT_SEQUENCE)
        sequence = ttk.Entry(run, textvariable=self.sequence_var, width=28)
        sequence.grid(row=0, column=1, sticky="w", padx=(4, 10))
        ttk.Label(run, text="Repeats").grid(row=0, column=2, sticky="w")
        repeats = ttk.Entry(run, textvariable=self.values["repeats"], width=5)
        repeats.grid(row=0, column=3, sticky="w", padx=(4, 10))
        self.inputs += [(sequence, "normal"), (repeats, "normal")]
        self.start_button = ttk.Button(run, text="Start", command=self.start, width=10)
        self.start_button.grid(row=0, column=4, padx=2)
        self.stop_button = ttk.Button(run, text="Stop", command=self.stop, width=8, state="disabled")
        self.stop_button.grid(row=0, column=5, padx=2)
        self.save_plot_button = ttk.Button(run, text="Save plot", command=self.save_plot, width=10)
        self.save_plot_button.grid(row=0, column=6, padx=2)
        self.progress = ttk.Progressbar(run, length=160)
        self.progress.grid(row=0, column=7, padx=(10, 6))
        self.status_var = tk.StringVar(value="Connect the instruments, check the settings, then Start")
        status = ttk.Label(run, textvariable=self.status_var)
        status.grid(row=0, column=8, sticky="ew")
        self.safety_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            run, variable=self.safety_var,
            text=("Sample, wiring, pulse current, compliance, field and rotator clearance confirmed safe; "
                  "no other program is driving the PPMS, 6221 or SR830"),
        ).grid(row=1, column=0, columnspan=9, sticky="w", pady=(4, 0))

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

    # -- events from threads --------------------------------------------
    def post(self, kind, payload=None):
        self.events.put((kind, payload))

    def log_from_thread(self, message):
        message = str(message)
        quiet = message.startswith(("PPMS motor GetPPMSItem", "PPMS motor: angle="))
        self.post("file-log" if quiet else "log", message)

    def _drain(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "file-log":
                    self._append_log(payload, show=False)
                elif kind == "status":
                    self.status_var.set(payload)
                    self._append_log(payload)
                elif kind == "point":
                    self._add_point(payload)
                elif kind == "call":
                    payload()
        except queue.Empty:
            pass
        try:
            self.root.after(100, self._drain)
        except tk.TclError:
            pass

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

    def _in_thread(self, label, work, done=None):
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
        if done is not None:
            done(result, error)
        elif error is not None:
            self.status_var.set(core.short_status_error(error, 160))
            messagebox.showerror(APP_TITLE, str(error), parent=self.root)

    # -- connections ----------------------------------------------------
    def connect_all(self):
        try:
            port = int(self.port_var.get())
            compliance = _number(self.values["compliance_v"].get(), "6221 compliance (V)")
            core.Keithley6221Controller._validate(0.0, 30e-6, compliance, 1)
        except ValueError as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self.root)
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
                    ("sr830", lambda: self.lockin.connect(lockin_resource))):
                try:
                    result = connect()
                    if key == "ppms":
                        report[key] = (True, f"Connected: {result['temperature_k']:.4g} K, "
                                             f"{result['field_oe']:+.6g} Oe / {result['field_status']}")
                    else:
                        report[key] = (True, f"Connected: {str(result)[:60]}")
                except Exception as error:
                    report[key] = (False, f"Failed: {core.short_status_error(error, 110)}")
                    self.log_from_thread(f"{key} connection failed: {error}")
            return report

        def done(report, error):
            if error is not None:
                messagebox.showerror(APP_TITLE, str(error), parent=self.root)
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
                messagebox.showwarning(APP_TITLE, "; ".join(problems) or str(error), parent=self.root)
            self.status_var.set("Disconnected (6221 output off; field and angle unchanged)")

        self._in_thread("Disconnecting…", work, done)

    # -- run ------------------------------------------------------------
    def current_settings(self):
        states = {name: tuple(variable.get() for variable in variables)
                  for name, variables in self.state_vars.items()}
        values = {key: variable.get() for key, variable in self.values.items()}
        return build_settings(states, self.sequence_var.get(), values, self.autorange_var.get())

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
            messagebox.showerror(APP_TITLE, str(error), parent=self.root)
            return
        if not messagebox.askyesno(APP_TITLE, self.summary(settings), parent=self.root, default="no"):
            return
        try:
            self.run_folder = DATA_DIRECTORY / datetime.now().strftime("%Y%m%d_%H%M%S")
            self.run_folder.mkdir(parents=True, exist_ok=False)
            record = asdict(settings)
            record["states"] = {name: dict(angle_deg=a, write_field_oe=h, pulse_ma=i)
                                for name, (a, h, i) in settings.states.items()}
            record["instruments"] = {"ppms_host": self.host_var.get(), "6221": self.source.identity,
                                     "sr830": self.lockin.identity}
            (self.run_folder / "settings.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
            self.csv_file = open(self.run_folder / "MS-fourState.csv", "w", newline="", encoding="utf-8")
            self.csv_writer = csv.DictWriter(self.csv_file, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            self.csv_writer.writeheader()
            self.csv_file.flush()
        except OSError as error:
            messagebox.showerror(APP_TITLE, f"Could not create the data folder: {error}", parent=self.root)
            return
        self.rows = []
        self.plot.set_rows([])
        self.progress.configure(maximum=len(settings.steps), value=0)
        self.stop_event.clear()
        self._set_busy(True, running=True)
        self._append_log(f"Run folder: {self.run_folder}")
        run = FourStateRun(self.ppms, self.source, self.lockin, settings, emit=self.post, stop=self.stop_event)

        def target():
            try:
                run.run()
                outcome = None
            except core.PPMSScanCancelled as error:
                outcome = ("stopped", error)
            except BaseException as error:
                outcome = ("failed", error)
            self.post("call", lambda: self._run_finished(outcome))

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def summary(self, s):
        lines = ["Run this sequence?", ""]
        for index, (cycle, name) in enumerate(s.steps[:12], 1):
            angle, field, pulse = s.states[name]
            lines.append(f"{index:>2}. {name:<2}  φ {angle:g}°,  H {field:+g} Oe,  pulse {pulse:+g} mA")
        if len(s.steps) > 12:
            lines.append(f"    … {len(s.steps)} steps in all")
        rotations = 0
        previous = None
        for _cycle, name in s.steps:
            angle = s.states[name][0]
            if previous is not None and abs(angle - previous) > LARGE_ROTATION_DEG:
                rotations += 1
            previous = angle
        lines += [
            "",
            f"Pulse: {s.pulse_width_s * 1e6:g} µs × {s.pulse_count}, compliance {s.compliance_v:g} V",
            f"Field: {s.field_rate_oe_s:g} Oe/s, Driven, settle {s.field_settle_s:g} s",
            f"Read at 0 Oe: {s.read_current_rms_a * 1e3:g} mA RMS at {s.read_frequency_hz:g} Hz, "
            f"{s.read_time_s:g} s, SR830 1st harmonic"
            + (", autorange" if s.autorange else ""),
            "",
            f"The rotator turns at zero field, including {rotations} large rotation(s) between steps"
            " (plus one at the start if it is not at the first angle). It has no software stop:"
            " keep MultiVu Motion Control at hand.",
            "At the end, on Stop or on an error: 6221 off, field to 0 Oe (Driven); the rotator stays.",
        ]
        return "\n".join(lines)

    def _add_point(self, row):
        self.rows.append(row)
        if self.csv_writer is not None:
            self.csv_writer.writerow(row)
            self.csv_file.flush()
        self.plot.set_rows(self.rows)
        self.progress.configure(value=len(self.rows))
        self.last_var.set(
            f"Last read: step {row['step']}, state {row['state']}\n"
            f"X = {row['x_v'] * 1e6:+.4g} µV   Y = {row['y_v'] * 1e6:+.4g} µV\n"
            f"θ = {row['theta_deg']:+.2f}°   R'xy = {row['rxy_ohm'] * 1e3:+.4g} mΩ\n"
            f"H read {row['read_field_oe']:+.4g} Oe, T = {row['temperature_k']:.4g} K")

    def _run_finished(self, outcome):
        self._set_busy(False)
        if self.csv_file is not None:
            self.csv_file.close()
            self.csv_file = None
            self.csv_writer = None
        if self.rows:
            self.save_plot(quiet=True)
        if outcome is None:
            self.status_var.set(f"Finished: {len(self.rows)} points saved in {self.run_folder}")
        elif outcome[0] == "stopped":
            self.status_var.set(f"Stopped after {len(self.rows)} points; 6221 off. "
                                "If the rotator was moving, check MultiVu")
        else:
            self.status_var.set(f"Failed after {len(self.rows)} points: {core.short_status_error(outcome[1], 120)}")
            messagebox.showerror(APP_TITLE, str(outcome[1]), parent=self.root)
        self._append_log(self.status_var.get())
        self.worker = None

    def stop(self):
        if self.worker is not None:
            self.stop_event.set()
            self.status_var.set("Stopping: 6221 off and field to 0 Oe after the current action…")

    def save_plot(self, quiet=False):
        folder = self.run_folder or DATA_DIRECTORY
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "MS-fourState_plot.eps"
            self.canvas.update_idletasks()
            self.canvas.postscript(file=str(path), colormode="color")
        except (OSError, tk.TclError) as error:
            if not quiet:
                messagebox.showerror(APP_TITLE, f"Could not save the plot: {error}", parent=self.root)
            return
        if not quiet:
            self.status_var.set(f"Plot saved: {path}")

    def close(self):
        if self.worker is not None:
            messagebox.showwarning(APP_TITLE, "Stop the run and wait for it to finish before closing.",
                                   parent=self.root)
            return
        for controller in (self.source, self.lockin, self.ppms):
            try:
                controller.disconnect()
            except Exception:
                pass
        self.root.destroy()


def main():
    root = tk.Tk()
    FourStateApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
