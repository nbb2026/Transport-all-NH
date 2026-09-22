"""Keithley 6221 AC-current sweep with SR830 voltage averaging.

The 6221 waveform amplitude command uses peak current. All current settings and
plot axes in this program use RMS current, and are converted to peak only when a
command is sent to the instrument.

Before the formal sweep, the program runs 0.1 mA RMS for 30 seconds. Those
samples are saved in the raw CSV as preconditioning data but are excluded from
the I-V curve and summary by an explicit measurement-stage filter.

The settings window appears first. After Start Formal Recording is clicked,
the user selects an output parent folder and a timestamped results directory
is created inside it.

For every formal current point the program:
1. updates and restarts the 6221 sine waveform;
2. waits for the SR830 output to settle;
3. reads the SR830 at a user-selected sampling rate and duration;
4. calculates the mean and population standard deviation;
5. updates cumulative R/theta time plots plus an I-V plot and saves
   raw/summary CSV files.

Closing the plot, pressing Ctrl+C, or raising an exception stops the 6221
waveform, turns its output off, stops all SR830 queries, and closes VISA.
"""

import csv
import math
import statistics
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import matplotlib.pyplot as plt
import pyvisa


# ----------------------------- VISA addresses -----------------------------
KEITHLEY_6221_ADDRESS = "GPIB0::30::INSTR"
SR830_ADDRESS = "GPIB0::8::INSTR"


# ----------------------------- Sweep settings -----------------------------
# All current values exposed to the user are RMS values.
START_CURRENT_RMS_MA = 0.1
STOP_CURRENT_RMS_MA = 3.0
STEP_CURRENT_RMS_MA = 0.1

SOURCE_FREQUENCY_HZ = 13.7
MIN_SOURCE_FREQUENCY_HZ = 1e-3
MAX_SOURCE_FREQUENCY_HZ = 1e5
COMPLIANCE_V = 50.0
MARKER_PHASE_DEG = 0.0
MARKER_LINE = 4

# Wait before beginning the user-selected averaging window. The current SR830
# setup uses a 1 s time constant, so this avoids the largest step transient.
SETTLE_TIME_S = 3.0
MEASURE_TIME_PER_POINT_S = 30.0
SR830_SAMPLE_FREQUENCY_HZ = 1.0  # one sample per second
MAX_SR830_SAMPLE_FREQUENCY_HZ = 100.0
MAX_MEASURE_TIME_PER_POINT_S = 86400.0

# Before the formal I-V sweep, run this current for a fixed period. These
# samples are stored in raw CSV only and are excluded from I-V statistics.
PRECONDITION_CURRENT_RMS_MA = 0.1
PRECONDITION_TIME_S = 30.0

# Voltage used for the I-V vertical axis: "X", "Y", or "R".
VOLTAGE_CHANNEL = "R"


# ----------------------------- SR830 settings -----------------------------
# These match the current standalone SR830_read.py configuration.
SR830_HARMONIC = 2
SR830_MIN_HARMONIC = 1
SR830_MAX_HARMONIC = 19999
SR830_MAX_DEMOD_FREQUENCY_HZ = 102000.0
SR830_INPUT_MODE = 1  # 0=A, 1=A-B, 2=I 1 MOhm, 3=I 100 MOhm
SR830_SENSITIVITY = 9  # 2 uV full scale in voltage-input mode
SR830_MAX_SENSITIVITY_INDEX = 26  # 1 V full scale
SR830_TIME_CONSTANT = 10  # 1 s
SR830_FILTER_SLOPE = 1  # 12 dB/oct


# ----------------------------- Safety limits ------------------------------
MAX_WAVE_CURRENT_PEAK_A = 0.105
MAX_COMPLIANCE_V = 105.0
MAX_SWEEP_POINTS = 10000


# ----------------------------- Data output --------------------------------
RESULTS_DIRECTORY_NAME_PREFIX = f"{Path(__file__).stem}_Results"


def select_voltage_display_unit(values_v):
    max_abs_v = max((abs(value) for value in values_v), default=0.0)
    if max_abs_v >= 1e-1:
        return 1.0, "V"
    if max_abs_v >= 1e-4:
        return 1e3, "mV"
    if max_abs_v >= 1e-7:
        return 1e6, "uV"
    return 1e9, "nV"
PRECONDITION_STAGE = "preconditioning"
FORMAL_SWEEP_STAGE = "iv_sweep"


def rms_to_peak(current_rms_a):
    return math.sqrt(2.0) * current_rms_a


def peak_to_rms(current_peak_a):
    return current_peak_a / math.sqrt(2.0)


def normalize_sr830_harmonic(value):
    """Round to the nearest integer, then return its absolute value."""
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise ValueError("SR830 harmonic must be a finite number.")
    return int(math.floor(abs(numeric_value) + 0.5))


def write(inst, command, delay_s=0.05):
    inst.write(command)
    if delay_s > 0:
        time.sleep(delay_s)


def query_text(inst, command):
    return inst.query(command).strip()


def query_float(inst, command):
    return float(query_text(inst, command))


def verify_identity(inst, expected_model, address):
    identity = query_text(inst, "*IDN?")
    if expected_model.upper() not in identity.upper():
        raise RuntimeError(
            f"Unexpected instrument at {address}. "
            f"Expected {expected_model}, received: {identity}"
        )
    return identity


def assert_no_6221_error(source, context):
    error = query_text(source, "SYST:ERR?")
    error_code = int(error.split(",", 1)[0].strip())
    if error_code != 0:
        raise RuntimeError(f"6221 SCPI error after {context}: {error}")


def write_6221_checked(source, command):
    """Send one 6221 command and identify that command if it is rejected."""
    write(source, command)
    assert_no_6221_error(source, repr(command))


def write_sr830_checked(lockin, command):
    """Send one SR830 command and check command/execution error bits."""
    write(lockin, command)
    event_status = int(query_text(lockin, "*ESR?"))
    if event_status & 0x30:
        raise RuntimeError(
            f"SR830 rejected {command!r}; *ESR? returned {event_status}"
        )


def open_instruments():
    resource_manager = pyvisa.ResourceManager()
    opened = []
    try:
        source = resource_manager.open_resource(KEITHLEY_6221_ADDRESS)
        opened.append(source)
        lockin = resource_manager.open_resource(SR830_ADDRESS)
        opened.append(lockin)

        for instrument in opened:
            instrument.timeout = 5000
            instrument.write_termination = "\n"
            instrument.read_termination = "\n"

        # The SR830 manual requires OUTX 1 at the start of a GPIB program.
        write(lockin, "OUTX 1")
        source_identity = verify_identity(
            source, "6221", KEITHLEY_6221_ADDRESS
        )
        lockin_identity = verify_identity(lockin, "SR830", SR830_ADDRESS)
        print("Connected:", source_identity)
        print("Connected:", lockin_identity)
        return resource_manager, source, lockin
    except BaseException:
        for instrument in reversed(opened):
            try:
                instrument.close()
            except Exception:
                pass
        resource_manager.close()
        raise


def validate_settings(
    start_current_rms_ma=START_CURRENT_RMS_MA,
    stop_current_rms_ma=STOP_CURRENT_RMS_MA,
    step_current_rms_ma=STEP_CURRENT_RMS_MA,
    source_frequency_hz=SOURCE_FREQUENCY_HZ,
    sr830_sample_frequency_hz=SR830_SAMPLE_FREQUENCY_HZ,
    measure_time_per_point_s=MEASURE_TIME_PER_POINT_S,
    sr830_harmonic=SR830_HARMONIC,
):
    sr830_harmonic = normalize_sr830_harmonic(sr830_harmonic)
    if not SR830_MIN_HARMONIC <= sr830_harmonic <= SR830_MAX_HARMONIC:
        raise ValueError(
            f"SR830 harmonic must round to an integer within "
            f"{SR830_MIN_HARMONIC}–{SR830_MAX_HARMONIC}."
        )
    if (
        not math.isfinite(sr830_sample_frequency_hz)
        or not 0
        < sr830_sample_frequency_hz
        <= MAX_SR830_SAMPLE_FREQUENCY_HZ
    ):
        raise ValueError(
            f"SR830 sampling frequency must be within "
            f"(0, {MAX_SR830_SAMPLE_FREQUENCY_HZ:g}] Hz."
        )
    if (
        not math.isfinite(measure_time_per_point_s)
        or not 0
        < measure_time_per_point_s
        <= MAX_MEASURE_TIME_PER_POINT_S
    ):
        raise ValueError(
            f"Measurement time per point must be within "
            f"(0, {MAX_MEASURE_TIME_PER_POINT_S:g}] s."
        )
    if SETTLE_TIME_S < 0:
        raise ValueError("SETTLE_TIME_S must be >= 0.")
    if PRECONDITION_TIME_S <= 0:
        raise ValueError("PRECONDITION_TIME_S must be > 0.")
    if PRECONDITION_CURRENT_RMS_MA <= 0:
        raise ValueError("PRECONDITION_CURRENT_RMS_MA must be > 0.")
    if (
        not math.isfinite(source_frequency_hz)
        or not MIN_SOURCE_FREQUENCY_HZ
        <= source_frequency_hz
        <= MAX_SOURCE_FREQUENCY_HZ
    ):
        raise ValueError(
            f"6221 frequency must be within "
            f"{MIN_SOURCE_FREQUENCY_HZ:g}–"
            f"{MAX_SOURCE_FREQUENCY_HZ:g} Hz."
        )
    demod_frequency_hz = source_frequency_hz * sr830_harmonic
    if demod_frequency_hz > SR830_MAX_DEMOD_FREQUENCY_HZ:
        raise ValueError(
            f"{sr830_harmonic}ω demodulation frequency would be "
            f"{demod_frequency_hz:g} Hz, above the SR830 limit of "
            f"{SR830_MAX_DEMOD_FREQUENCY_HZ:g} Hz."
        )
    if not 0 < COMPLIANCE_V <= MAX_COMPLIANCE_V:
        raise ValueError(
            f"COMPLIANCE_V must be within (0, {MAX_COMPLIANCE_V}] V."
        )
    if not 0 <= MARKER_PHASE_DEG <= 360:
        raise ValueError("MARKER_PHASE_DEG must be between 0 and 360.")
    if not 1 <= MARKER_LINE <= 6:
        raise ValueError("MARKER_LINE must be between 1 and 6.")
    if MARKER_LINE == 2:
        raise ValueError(
            "Marker line 2 normally conflicts with the default trigger output."
        )
    if VOLTAGE_CHANNEL not in {"X", "Y", "R"}:
        raise ValueError("VOLTAGE_CHANNEL must be 'X', 'Y', or 'R'.")

    currents_ma = generate_sweep_values(
        start_current_rms_ma,
        stop_current_rms_ma,
        step_current_rms_ma,
    )
    if min(currents_ma) <= 0:
        raise ValueError(
            "AC RMS current values must be > 0 mA; the 6221 waveform "
            "amplitude cannot be zero."
        )

    largest_peak_a = max(rms_to_peak(value * 1e-3) for value in currents_ma)
    largest_peak_a = max(
        largest_peak_a,
        rms_to_peak(PRECONDITION_CURRENT_RMS_MA * 1e-3),
    )
    if largest_peak_a > MAX_WAVE_CURRENT_PEAK_A:
        maximum_rms_ma = peak_to_rms(MAX_WAVE_CURRENT_PEAK_A) * 1e3
        raise ValueError(
            f"Requested current exceeds the 6221 waveform limit of "
            f"{maximum_rms_ma:.6g} mA RMS."
        )
    return currents_ma


def ask_output_parent_directory():
    """Ask where this run's timestamped results folder should be created."""
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    try:
        selected = filedialog.askdirectory(
            parent=root,
            title="Choose folder for 6221–SR830 measurement results",
            initialdir=str(Path(__file__).resolve().parent),
            mustexist=True,
        )
    finally:
        root.destroy()
    if not selected:
        return None
    return Path(selected).expanduser().resolve()


def ask_sweep_settings():
    """Collect AC RMS sweep limits before any instrument is connected."""
    root = tk.Tk()
    root.title("6221–SR830 I–V sweep")
    root.resizable(False, False)

    start_var = tk.StringVar(value=f"{START_CURRENT_RMS_MA:g}")
    stop_var = tk.StringVar(value=f"{STOP_CURRENT_RMS_MA:g}")
    step_var = tk.StringVar(value=f"{STEP_CURRENT_RMS_MA:g}")
    frequency_var = tk.StringVar(value=f"{SOURCE_FREQUENCY_HZ:g}")
    sample_frequency_var = tk.StringVar(
        value=f"{SR830_SAMPLE_FREQUENCY_HZ:g}"
    )
    point_time_var = tk.StringVar(value=f"{MEASURE_TIME_PER_POINT_S:g}")
    harmonic_var = tk.StringVar(value=str(SR830_HARMONIC))
    summary_var = tk.StringVar(value="")
    result = {"values": None}

    frame = ttk.Frame(root, padding=16)
    frame.grid(row=0, column=0)

    ttk.Label(
        frame,
        text="Keithley 6221 AC-current sweep (RMS)",
        font=("", 11, "bold"),
    ).grid(row=0, column=0, columnspan=3, pady=(0, 12))

    fields = (
        ("Start current", start_var, "mA RMS"),
        ("Stop current", stop_var, "mA RMS"),
        ("Step magnitude", step_var, "mA RMS"),
        ("6221 frequency", frequency_var, "Hz"),
        ("SR830 sampling rate", sample_frequency_var, "Hz"),
        ("Time per current point", point_time_var, "s"),
    )
    for row, (label, variable, unit) in enumerate(fields, start=1):
        ttk.Label(frame, text=label).grid(
            row=row, column=0, sticky="w", pady=4
        )
        ttk.Entry(
            frame, textvariable=variable, width=14, justify="right"
        ).grid(row=row, column=1, padx=(12, 5), pady=4)
        ttk.Label(frame, text=unit).grid(
            row=row, column=2, sticky="w", pady=4
        )

    ttk.Label(frame, text="SR830 harmonic").grid(
        row=7, column=0, sticky="w", pady=4
    )
    harmonic_entry = ttk.Entry(
        frame,
        textvariable=harmonic_var,
        width=14,
        justify="right",
    )
    harmonic_entry.grid(row=7, column=1, padx=(12, 5), pady=4)
    ttk.Label(frame, text="× reference").grid(
        row=7, column=2, sticky="w", pady=4
    )

    ttk.Label(
        frame,
        textvariable=summary_var,
        foreground="#555555",
        justify="left",
    ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(10, 12))

    def parsed_values():
        return (
            float(start_var.get()),
            float(stop_var.get()),
            float(step_var.get()),
            float(frequency_var.get()),
            float(sample_frequency_var.get()),
            float(point_time_var.get()),
            normalize_sr830_harmonic(harmonic_var.get()),
        )

    def normalize_harmonic_entry(_event=None):
        try:
            harmonic_var.set(
                str(normalize_sr830_harmonic(harmonic_var.get()))
            )
        except (TypeError, ValueError):
            pass

    def refresh_summary(*_args):
        try:
            settings = parsed_values()
            values = validate_settings(*settings)
            total_time_s = len(values) * (
                SETTLE_TIME_S + settings[5]
            ) + PRECONDITION_TIME_S
            summary_var.set(
                f"{len(values)} current points; nominal measurement time "
                f"{total_time_s / 60:.1f} min\n"
                f"Pre-run: {PRECONDITION_CURRENT_RMS_MA:g} mA RMS for "
                f"{PRECONDITION_TIME_S:g} s (excluded from I-V)\n"
                f"6221 waveform: {settings[3]:.6g} Hz; "
                f"SR830: {settings[6]}ω "
                f"({settings[3] * settings[6]:.6g} Hz); "
                f"{settings[5]:.6g} s per point at "
                f"{settings[4]:.6g} Hz SR830 sampling "
                f"({1 / settings[4]:.6g} s interval)"
            )
        except Exception as exc:
            summary_var.set(f"Parameter error: {exc}")

    def start_measurement():
        try:
            values = parsed_values()
            harmonic_var.set(str(values[6]))
            validate_settings(*values)
        except Exception as exc:
            messagebox.showerror("Invalid sweep settings", str(exc))
            return
        result["values"] = values
        root.destroy()

    def cancel():
        result["values"] = None
        root.destroy()

    button_frame = ttk.Frame(frame)
    button_frame.grid(row=9, column=0, columnspan=3)
    ttk.Button(
        button_frame, text="Start Formal Recording", command=start_measurement
    ).grid(row=0, column=0, padx=5)
    ttk.Button(button_frame, text="Cancel", command=cancel).grid(
        row=0, column=1, padx=5
    )

    for variable in (
        start_var,
        stop_var,
        step_var,
        frequency_var,
        sample_frequency_var,
        point_time_var,
        harmonic_var,
    ):
        variable.trace_add("write", refresh_summary)
    harmonic_entry.bind("<FocusOut>", normalize_harmonic_entry)
    root.protocol("WM_DELETE_WINDOW", cancel)
    root.bind("<Return>", lambda _event: start_measurement())
    refresh_summary()
    root.mainloop()
    return result["values"]


def generate_sweep_values(start, stop, step):
    if not all(math.isfinite(value) for value in (start, stop, step)):
        raise ValueError("Sweep start, stop, and step must be finite numbers.")
    if step <= 0:
        raise ValueError("STEP_CURRENT_RMS_MA must be > 0.")

    span = abs(stop - start)
    point_intervals = span / step
    if not math.isfinite(span) or not math.isfinite(point_intervals):
        raise ValueError("Sweep range divided by step is too large.")
    estimated_points = int(math.floor(point_intervals)) + 2
    if estimated_points > MAX_SWEEP_POINTS:
        raise ValueError(
            f"Sweep would contain about {estimated_points} points; "
            f"the safety limit is {MAX_SWEEP_POINTS}."
        )

    direction = 1.0 if stop >= start else -1.0
    signed_step = direction * step
    values = []
    current = start
    tolerance = abs(step) * 1e-9

    if direction > 0:
        while current <= stop + tolerance:
            values.append(current)
            current += signed_step
    else:
        while current >= stop - tolerance:
            values.append(current)
            current += signed_step

    if not values or not math.isclose(values[-1], stop, abs_tol=tolerance):
        values.append(stop)
    else:
        values[-1] = stop
    return values


def setup_6221(source, initial_current_rms_ma, source_frequency_hz):
    initial_peak_a = rms_to_peak(initial_current_rms_ma * 1e-3)

    write(source, "*RST")
    write(source, "*CLS")
    for command in (
        f"CURR:COMP {COMPLIANCE_V:.12g}",
        "SOUR:WAVE:FUNC SIN",
        f"SOUR:WAVE:FREQ {source_frequency_hz:.12g}",
        f"SOUR:WAVE:AMPL {initial_peak_a:.12e}",
        "SOUR:WAVE:OFFS 0",
        "SOUR:WAVE:RANG BEST",
        f"SOUR:WAVE:PMAR {MARKER_PHASE_DEG:.12g}",
        f"SOUR:WAVE:PMAR:OLIN {MARKER_LINE}",
        "SOUR:WAVE:PMAR:STAT ON",
        "OUTP OFF",
    ):
        write_6221_checked(source, command)


def set_6221_current(source, current_rms_ma):
    current_peak_a = rms_to_peak(current_rms_ma * 1e-3)

    for command in (
        "SOUR:WAVE:ABOR",
        f"SOUR:WAVE:AMPL {current_peak_a:.12e}",
        "SOUR:WAVE:ARM",
        "SOUR:WAVE:INIT",
    ):
        write_6221_checked(source, command)

    output_on = int(query_float(source, "OUTP?")) == 1
    if not output_on:
        raise RuntimeError("6221 output did not turn ON.")

    peak_readback_a = query_float(source, "SOUR:WAVE:AMPL?")
    return peak_to_rms(peak_readback_a) * 1e3


def stop_6221(source):
    errors = []
    for command in ("SOUR:WAVE:ABOR", "CURR 0", "OUTP OFF", "*RST", "*CLS"):
        try:
            write(source, command)
        except Exception as exc:
            errors.append(f"{command}: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))


def setup_sr830(lockin, sr830_harmonic):
    write(lockin, "*CLS")
    for command in (
        "FMOD 0",  # external reference
        "RSLP 2",  # 6221 Trigger Link phase marker: TTL falling edge
        f"HARM {sr830_harmonic}",
        f"ISRC {SR830_INPUT_MODE}",
        "IGND 0",  # input shield float
        "ICPL 0",  # AC coupling
        "ILIN 1",  # line notch
        f"SENS {SR830_SENSITIVITY}",
        f"OFLT {SR830_TIME_CONSTANT}",
        f"OFSL {SR830_FILTER_SLOPE}",
        "RMOD 1",
        "PHAS 0",
        "DDEF 1,1,0",  # Channel 1 front-panel display: R, no ratio
        "DDEF 2,1,0",  # Channel 2 front-panel display: theta, no ratio
    ):
        write_sr830_checked(lockin, command)

    channel_1_display = tuple(
        int(float(value))
        for value in query_text(lockin, "DDEF? 1").split(",")
    )
    channel_2_display = tuple(
        int(float(value))
        for value in query_text(lockin, "DDEF? 2").split(",")
    )
    if channel_1_display != (1, 0) or channel_2_display != (1, 0):
        raise RuntimeError(
            "SR830 front-panel display verification failed: "
            f"CH1={channel_1_display}, CH2={channel_2_display}"
        )
    reference_mode = int(query_float(lockin, "FMOD?"))
    reference_slope = int(query_float(lockin, "RSLP?"))
    if reference_mode != 0 or reference_slope != 2:
        raise RuntimeError(
            "SR830 external-reference readback mismatch: "
            f"FMOD={reference_mode}, RSLP={reference_slope}"
        )

    actual_harmonic = int(query_float(lockin, "HARM?"))
    if actual_harmonic != sr830_harmonic:
        raise RuntimeError(
            "SR830 harmonic readback mismatch: "
            f"requested {sr830_harmonic}, received {actual_harmonic}"
        )
    reference_frequency_hz = query_float(lockin, "FREQ?")
    print("SR830 front panel: Channel 1 = R, Channel 2 = theta")
    print(f"SR830 harmonic: {actual_harmonic}")
    print(f"SR830 reference frequency: {reference_frequency_hz:.6g} Hz")
    return actual_harmonic


def increase_sr830_range_one_step(lockin):
    """Increase the SR830 full-scale input range by exactly one step."""
    old_index = int(query_float(lockin, "SENS?"))
    if old_index >= SR830_MAX_SENSITIVITY_INDEX:
        return old_index, old_index, False, "already at maximum range"

    requested_index = old_index + 1
    write_sr830_checked(lockin, f"SENS {requested_index}")
    new_index = int(query_float(lockin, "SENS?"))
    if new_index != requested_index:
        raise RuntimeError(
            "SR830 sensitivity readback mismatch after auto range: "
            f"requested {requested_index}, received {new_index}"
        )
    return (
        old_index,
        new_index,
        True,
        f"auto range SENS {old_index} -> {new_index}",
    )


def auto_range_sr830_if_overloaded(
    lockin, sample, current_sensitivity_index
):
    """Apply at most one sensitivity step for one overloaded sample."""
    sensitivity_index_used = current_sensitivity_index
    auto_range_action = ""
    range_changed = False

    if sample["lia_status"] & 0x07:
        (
            _old_sensitivity_index,
            current_sensitivity_index,
            range_changed,
            auto_range_action,
        ) = increase_sr830_range_one_step(lockin)
        print(f"  SR830 overload: {auto_range_action}")
        sample["status_text"] += f"; {auto_range_action}"

    return (
        current_sensitivity_index,
        sensitivity_index_used,
        range_changed,
        auto_range_action,
    )


def parse_lia_status(status):
    status = int(status)
    messages = []
    if status & (1 << 0):
        messages.append("INPUT/RESERVE OVERLOAD")
    if status & (1 << 1):
        messages.append("FILTER OVERLOAD")
    if status & (1 << 2):
        messages.append("OUTPUT OVERLOAD")
    if status & (1 << 3):
        messages.append("REFERENCE UNLOCK")
    return messages or ["OK"]


def read_sr830(lockin):
    response = query_text(lockin, "SNAP? 1,2,3,4,9")
    values = [float(value) for value in response.split(",")]
    if len(values) != 5:
        raise RuntimeError(f"Unexpected SR830 SNAP response: {response}")

    x_v, y_v, r_v, phase_deg, reference_frequency_hz = values
    lia_status = int(query_float(lockin, "LIAS?"))
    status_messages = parse_lia_status(lia_status)
    return {
        "X": x_v,
        "Y": y_v,
        "R": r_v,
        "phase_deg": phase_deg,
        "reference_frequency_hz": reference_frequency_hz,
        "lia_status": lia_status,
        "status_text": ", ".join(status_messages),
    }


def create_output_files(output_parent_directory):
    output_parent_directory = Path(output_parent_directory)
    if not output_parent_directory.is_dir():
        raise ValueError(
            f"Output folder does not exist: {output_parent_directory}"
        )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_directory = output_parent_directory / (
        f"{RESULTS_DIRECTORY_NAME_PREFIX}_{stamp}"
    )
    results_directory.mkdir(parents=True, exist_ok=False)
    raw_path = results_directory / f"6221_SR830_IV_raw_{stamp}.csv"
    summary_path = (
        results_directory / f"6221_SR830_IV_summary_{stamp}.csv"
    )
    figure_path = results_directory / f"6221_SR830_IV_{stamp}.png"

    raw_file = raw_path.open("x", newline="", encoding="utf-8")
    summary_file = summary_path.open("x", newline="", encoding="utf-8")
    return raw_path, summary_path, figure_path, raw_file, summary_file


def select_formal_samples_for_statistics(samples):
    """Return only formal-sweep samples; never average pre-run data."""
    formal_samples = [
        item
        for item in samples
        if item.get("measurement_stage") == FORMAL_SWEEP_STAGE
    ]
    if not formal_samples:
        raise RuntimeError("No formal SR830 samples available for averaging.")
    excluded_count = len(samples) - len(formal_samples)
    if excluded_count:
        print(
            f"Excluded {excluded_count} non-formal SR830 samples "
            "from I-V statistics."
        )
    return formal_samples


def create_plot(sr830_harmonic, source_frequency_hz):
    plt.ion()
    figure = plt.figure(figsize=(13, 7))
    sweep_plot_name = "I²–V" if sr830_harmonic == 2 else "I–V"
    figure.suptitle(
        f"6221–SR830 {sweep_plot_name} measurement  |  "
        f"6221 frequency: {source_frequency_hz:.9g} Hz  |  "
        f"SR830: {sr830_harmonic}ω",
        y=0.975,
    )
    grid = figure.add_gridspec(
        2,
        2,
        width_ratios=(1.0, 1.15),
        hspace=0.10,
        wspace=0.28,
    )
    r_time_axis = figure.add_subplot(grid[0, 0])
    theta_time_axis = figure.add_subplot(
        grid[1, 0], sharex=r_time_axis
    )
    iv_axis = figure.add_subplot(grid[:, 1])

    r_time_line, = r_time_axis.plot(
        [], [], marker=".", linestyle="-", color="tab:blue"
    )
    theta_time_line, = theta_time_axis.plot(
        [], [], marker=".", linestyle="-", color="tab:orange"
    )
    r_time_axis.set_ylabel(f"SR830 R ({sr830_harmonic}ω) (V)")
    r_time_axis.grid(True)
    r_time_axis.tick_params(labelbottom=False)
    theta_time_axis.set_xlabel("Elapsed measurement time (s)")
    theta_time_axis.set_ylabel(
        f"SR830 theta ({sr830_harmonic}ω) (deg)"
    )
    theta_time_axis.grid(True)

    if sr830_harmonic == 2:
        iv_axis.set_xlabel("Keithley 6221 current RMS squared (mA²)")
    else:
        iv_axis.set_xlabel("Keithley 6221 current RMS (mA)")
    iv_axis.set_ylabel(
        f"Mean SR830 {VOLTAGE_CHANNEL} voltage "
        f"({sr830_harmonic}ω) (V)"
    )
    iv_axis.grid(True)

    status_text = figure.text(0.02, 0.02, "Ready")
    figure.subplots_adjust(
        left=0.08,
        right=0.97,
        bottom=0.13,
        top=0.88,
    )
    return (
        figure,
        r_time_axis,
        theta_time_axis,
        iv_axis,
        r_time_line,
        theta_time_line,
        status_text,
    )


def wait_with_gui(duration_s, figure, state):
    end_time = time.monotonic() + duration_s
    while time.monotonic() < end_time:
        if state["stop_requested"] or not plt.fignum_exists(figure.number):
            state["stop_requested"] = True
            return False
        remaining = end_time - time.monotonic()
        plt.pause(min(0.05, max(0.001, remaining)))
    return True


def update_time_plot(
    figure,
    r_time_axis,
    theta_time_axis,
    r_time_line,
    theta_time_line,
    status_text,
    display_times,
    r_voltages,
    theta_values,
    current_rms_ma,
    stage_elapsed_s,
    stage_duration_s,
    sr830_harmonic,
    source_frequency_hz,
    stage_label="Measuring",
):
    voltage_scale, voltage_unit = select_voltage_display_unit(r_voltages)
    r_time_line.set_data(
        display_times,
        [value * voltage_scale for value in r_voltages],
    )
    theta_time_line.set_data(display_times, theta_values)
    r_time_axis.relim()
    r_time_axis.autoscale_view()
    r_time_axis.set_ylabel(
        f"SR830 R ({sr830_harmonic}ω) ({voltage_unit})"
    )
    theta_time_axis.relim()
    theta_time_axis.autoscale_view()
    status_text.set_text(
        f"{stage_label} {current_rms_ma:.6g} mA RMS | "
        f"6221 {source_frequency_hz:.9g} Hz | "
        f"SR830 {sr830_harmonic}ω | "
        f"{len(r_voltages)} samples | "
        f"{stage_elapsed_s:.1f}/{stage_duration_s:.1f} s"
    )
    figure.canvas.draw_idle()
    plt.pause(0.001)


def update_iv_plot(
    iv_axis,
    currents_ma,
    mean_voltages,
    std_voltages,
    sr830_harmonic,
    source_frequency_hz,
):
    iv_axis.clear()
    voltage_scale, voltage_unit = select_voltage_display_unit(mean_voltages)
    if sr830_harmonic == 2:
        x_values = [current_ma**2 for current_ma in currents_ma]
        x_label = "Keithley 6221 current RMS squared (mA²)"
        plot_name = "Voltage vs I²"
    else:
        x_values = currents_ma
        x_label = "Keithley 6221 current RMS (mA)"
        plot_name = "Voltage vs I"

    iv_axis.errorbar(
        x_values,
        [value * voltage_scale for value in mean_voltages],
        yerr=[value * voltage_scale for value in std_voltages],
        marker="o",
        linestyle="-",
        capsize=4,
        color="tab:red",
    )
    iv_axis.set_xlabel(x_label)
    iv_axis.set_ylabel(
        f"Mean SR830 {VOLTAGE_CHANNEL} voltage "
        f"({sr830_harmonic}ω) ({voltage_unit})"
    )
    iv_axis.set_title(
        f"{plot_name} | 6221 {source_frequency_hz:.9g} Hz | "
        f"SR830 {sr830_harmonic}ω"
    )
    iv_axis.grid(True)


def main():
    sweep_settings = ask_sweep_settings()
    if sweep_settings is None:
        print("Measurement cancelled before instrument connection.")
        return
    output_parent_directory = ask_output_parent_directory()
    if output_parent_directory is None:
        print("Formal recording cancelled before instrument connection.")
        return
    source_frequency_hz = sweep_settings[3]
    sr830_sample_frequency_hz = sweep_settings[4]
    measure_time_per_point_s = sweep_settings[5]
    sr830_harmonic = sweep_settings[6]
    sample_interval_s = 1.0 / sr830_sample_frequency_hz
    currents_rms_ma = validate_settings(*sweep_settings)
    (
        raw_path,
        summary_path,
        figure_path,
        raw_file,
        summary_file,
    ) = create_output_files(output_parent_directory)

    raw_writer = csv.writer(raw_file)
    raw_writer.writerow(
        [
            "timestamp",
            "measurement_stage",
            "point_index",
            "current_setpoint_rms_mA",
            "current_readback_rms_mA",
            "6221_source_frequency_Hz",
            "SR830_harmonic",
            "SR830_target_sampling_frequency_Hz",
            "requested_stage_duration_s",
            "point_elapsed_s",
            "X_V",
            "Y_V",
            "R_V",
            "phase_deg",
            "SR830_reference_frequency_Hz",
            "SR830_LIAS_status",
            "SR830_status_text",
            "SR830_sensitivity_index_used",
            "SR830_auto_range_action",
        ]
    )
    summary_writer = csv.writer(summary_file)
    summary_writer.writerow(
        [
            "point_index",
            "current_setpoint_rms_mA",
            "current_readback_rms_mA",
            "6221_source_frequency_Hz",
            "SR830_harmonic",
            "SR830_target_sampling_frequency_Hz",
            "requested_measurement_time_per_point_s",
            "actual_measurement_time_s",
            "achieved_sampling_frequency_Hz",
            "voltage_channel",
            "sample_count",
            "mean_voltage_V",
            "population_std_voltage_V",
            "mean_X_V",
            "mean_Y_V",
            "mean_R_V",
            "mean_phase_deg",
            "mean_reference_frequency_Hz",
            "overload_or_unlock_sample_count",
            "auto_range_adjustment_count",
            "final_SR830_sensitivity_index",
        ]
    )

    figure = None
    resource_manager = None
    source = None
    lockin = None
    state = {"stop_requested": False}
    shutdown_done = False
    completed = False

    iv_currents_ma = []
    iv_mean_voltages = []
    iv_std_voltages = []
    all_display_times = []
    all_display_r_voltages = []
    all_display_theta_values = []

    def shutdown_instruments():
        nonlocal shutdown_done
        if shutdown_done:
            return
        shutdown_done = True
        state["stop_requested"] = True

        if source is not None:
            try:
                stop_6221(source)
                print("6221 waveform aborted and output turned OFF.")
            except Exception as exc:
                print(f"WARNING: could not confirm 6221 shutdown: {exc}")

        for instrument in (lockin, source):
            if instrument is not None:
                try:
                    instrument.close()
                except Exception:
                    pass
        if resource_manager is not None:
            try:
                resource_manager.close()
            except Exception:
                pass
        print("SR830 polling stopped and VISA resources closed.")

    try:
        (
            resource_manager,
            source,
            lockin,
        ) = open_instruments()
        setup_6221(
            source,
            PRECONDITION_CURRENT_RMS_MA,
            source_frequency_hz,
        )
        source_frequency_readback_hz = query_float(
            source, "SOUR:WAVE:FREQ?"
        )
        print(
            f"6221 waveform frequency: "
            f"{source_frequency_readback_hz:.9g} Hz"
        )
        actual_sr830_harmonic = setup_sr830(lockin, sr830_harmonic)
        current_sr830_sensitivity_index = int(
            query_float(lockin, "SENS?")
        )

        (
            figure,
            r_time_axis,
            theta_time_axis,
            iv_axis,
            r_time_line,
            theta_time_line,
            status_text,
        ) = create_plot(
            actual_sr830_harmonic,
            source_frequency_readback_hz,
        )

        def on_close(_event):
            state["stop_requested"] = True
            shutdown_instruments()

        figure.canvas.mpl_connect("close_event", on_close)

        print(
            f"\nPre-run: {PRECONDITION_CURRENT_RMS_MA:.6g} mA RMS "
            f"for {PRECONDITION_TIME_S:.1f} s. "
            "These samples are excluded from I-V."
        )
        precondition_current_readback_rms_ma = set_6221_current(
            source, PRECONDITION_CURRENT_RMS_MA
        )
        display_start = time.monotonic()
        precondition_start = display_start
        precondition_auto_range_count = 0

        while time.monotonic() - precondition_start < PRECONDITION_TIME_S:
            if state["stop_requested"]:
                break

            sample_start = time.monotonic()
            sample = read_sr830(lockin)
            sample["measurement_stage"] = PRECONDITION_STAGE
            (
                current_sr830_sensitivity_index,
                sample_sensitivity_index,
                range_changed,
                auto_range_action,
            ) = auto_range_sr830_if_overloaded(
                lockin,
                sample,
                current_sr830_sensitivity_index,
            )
            if range_changed:
                precondition_auto_range_count += 1

            sample_time = time.monotonic()
            precondition_elapsed_s = sample_time - precondition_start
            display_elapsed_s = sample_time - display_start
            all_display_times.append(display_elapsed_s)
            all_display_r_voltages.append(sample["R"])
            all_display_theta_values.append(sample["phase_deg"])

            raw_writer.writerow(
                [
                    datetime.now().isoformat(timespec="milliseconds"),
                    PRECONDITION_STAGE,
                    0,
                    f"{PRECONDITION_CURRENT_RMS_MA:.12g}",
                    f"{precondition_current_readback_rms_ma:.12g}",
                    f"{source_frequency_readback_hz:.12g}",
                    actual_sr830_harmonic,
                    f"{sr830_sample_frequency_hz:.12g}",
                    f"{PRECONDITION_TIME_S:.12g}",
                    f"{precondition_elapsed_s:.6f}",
                    f"{sample['X']:.12e}",
                    f"{sample['Y']:.12e}",
                    f"{sample['R']:.12e}",
                    f"{sample['phase_deg']:.9g}",
                    f"{sample['reference_frequency_hz']:.12g}",
                    sample["lia_status"],
                    sample["status_text"],
                    sample_sensitivity_index,
                    auto_range_action,
                ]
            )
            raw_file.flush()

            update_time_plot(
                figure,
                r_time_axis,
                theta_time_axis,
                r_time_line,
                theta_time_line,
                status_text,
                all_display_times,
                all_display_r_voltages,
                all_display_theta_values,
                PRECONDITION_CURRENT_RMS_MA,
                precondition_elapsed_s,
                PRECONDITION_TIME_S,
                actual_sr830_harmonic,
                source_frequency_readback_hz,
                stage_label="Pre-running",
            )

            print(
                f"  pre-run t={precondition_elapsed_s:5.1f} s | "
                f"R={sample['R']:+.6e} V | "
                f"theta={sample['phase_deg']:+.3f} deg | "
                f"status={sample['status_text']}"
            )

            remaining_interval = (
                sample_interval_s - (time.monotonic() - sample_start)
            )
            if remaining_interval > 0 and not wait_with_gui(
                remaining_interval, figure, state
            ):
                break

        if not state["stop_requested"]:
            print(
                "Pre-run completed; starting formal I-V sweep. "
                f"Auto-range adjustments: {precondition_auto_range_count}"
            )

        for point_index, current_rms_ma in enumerate(currents_rms_ma, start=1):
            if state["stop_requested"]:
                break

            current_readback_rms_ma = set_6221_current(
                source, current_rms_ma
            )
            print(
                f"\nPoint {point_index}/{len(currents_rms_ma)}: "
                f"set {current_rms_ma:.6g} mA RMS, "
                f"readback {current_readback_rms_ma:.6g} mA RMS"
            )
            status_text.set_text(
                f"Settling at {current_rms_ma:.6g} mA RMS for "
                f"{SETTLE_TIME_S:.1f} s | "
                f"6221 {source_frequency_readback_hz:.9g} Hz"
            )
            figure.canvas.draw_idle()
            if not wait_with_gui(SETTLE_TIME_S, figure, state):
                break

            samples = []
            point_auto_range_adjustment_count = 0
            point_start = time.monotonic()

            while time.monotonic() - point_start < measure_time_per_point_s:
                if state["stop_requested"]:
                    break

                sample_start = time.monotonic()
                sample = read_sr830(lockin)
                sample["measurement_stage"] = FORMAL_SWEEP_STAGE
                (
                    current_sr830_sensitivity_index,
                    sample_sensitivity_index,
                    range_changed,
                    auto_range_action,
                ) = auto_range_sr830_if_overloaded(
                    lockin,
                    sample,
                    current_sr830_sensitivity_index,
                )
                if range_changed:
                    point_auto_range_adjustment_count += 1
                sample_time = time.monotonic()
                point_elapsed_s = sample_time - point_start
                display_elapsed_s = sample_time - display_start
                samples.append(sample)
                all_display_times.append(display_elapsed_s)
                all_display_r_voltages.append(sample["R"])
                all_display_theta_values.append(sample["phase_deg"])

                raw_writer.writerow(
                    [
                        datetime.now().isoformat(timespec="milliseconds"),
                        FORMAL_SWEEP_STAGE,
                        point_index,
                        f"{current_rms_ma:.12g}",
                        f"{current_readback_rms_ma:.12g}",
                        f"{source_frequency_readback_hz:.12g}",
                        actual_sr830_harmonic,
                        f"{sr830_sample_frequency_hz:.12g}",
                        f"{measure_time_per_point_s:.12g}",
                        f"{point_elapsed_s:.6f}",
                        f"{sample['X']:.12e}",
                        f"{sample['Y']:.12e}",
                        f"{sample['R']:.12e}",
                        f"{sample['phase_deg']:.9g}",
                        f"{sample['reference_frequency_hz']:.12g}",
                        sample["lia_status"],
                        sample["status_text"],
                        sample_sensitivity_index,
                        auto_range_action,
                    ]
                )
                raw_file.flush()

                update_time_plot(
                    figure,
                    r_time_axis,
                    theta_time_axis,
                    r_time_line,
                    theta_time_line,
                    status_text,
                    all_display_times,
                    all_display_r_voltages,
                    all_display_theta_values,
                    current_rms_ma,
                    point_elapsed_s,
                    measure_time_per_point_s,
                    actual_sr830_harmonic,
                    source_frequency_readback_hz,
                )

                print(
                    f"  t={point_elapsed_s:5.1f} s | "
                    f"{VOLTAGE_CHANNEL}="
                    f"{sample[VOLTAGE_CHANNEL]:+.6e} V | "
                    f"status={sample['status_text']}"
                )

                remaining_interval = (
                    sample_interval_s - (time.monotonic() - sample_start)
                )
                if remaining_interval > 0 and not wait_with_gui(
                    remaining_interval, figure, state
                ):
                    break

            if state["stop_requested"]:
                break
            if not samples:
                raise RuntimeError(
                    f"No valid SR830 samples at {current_rms_ma} mA RMS."
                )

            formal_samples = select_formal_samples_for_statistics(samples)

            selected = [
                item[VOLTAGE_CHANNEL] for item in formal_samples
            ]
            actual_measurement_time_s = time.monotonic() - point_start
            achieved_sampling_frequency_hz = (
                len(formal_samples) / actual_measurement_time_s
            )
            mean_voltage = statistics.fmean(selected)
            std_voltage = (
                statistics.pstdev(selected) if len(selected) > 1 else 0.0
            )
            mean_x = statistics.fmean(
                item["X"] for item in formal_samples
            )
            mean_y = statistics.fmean(
                item["Y"] for item in formal_samples
            )
            mean_r = statistics.fmean(
                item["R"] for item in formal_samples
            )
            mean_phase = statistics.fmean(
                item["phase_deg"] for item in formal_samples
            )
            mean_reference_frequency = statistics.fmean(
                item["reference_frequency_hz"]
                for item in formal_samples
            )
            bad_status_count = sum(
                item["lia_status"] != 0 for item in formal_samples
            )

            summary_writer.writerow(
                [
                    point_index,
                    f"{current_rms_ma:.12g}",
                    f"{current_readback_rms_ma:.12g}",
                    f"{source_frequency_readback_hz:.12g}",
                    actual_sr830_harmonic,
                    f"{sr830_sample_frequency_hz:.12g}",
                    f"{measure_time_per_point_s:.12g}",
                    f"{actual_measurement_time_s:.9g}",
                    f"{achieved_sampling_frequency_hz:.9g}",
                    VOLTAGE_CHANNEL,
                    len(formal_samples),
                    f"{mean_voltage:.12e}",
                    f"{std_voltage:.12e}",
                    f"{mean_x:.12e}",
                    f"{mean_y:.12e}",
                    f"{mean_r:.12e}",
                    f"{mean_phase:.9g}",
                    f"{mean_reference_frequency:.12g}",
                    bad_status_count,
                    point_auto_range_adjustment_count,
                    current_sr830_sensitivity_index,
                ]
            )
            summary_file.flush()

            iv_currents_ma.append(current_readback_rms_ma)
            iv_mean_voltages.append(mean_voltage)
            iv_std_voltages.append(std_voltage)
            update_iv_plot(
                iv_axis,
                iv_currents_ma,
                iv_mean_voltages,
                iv_std_voltages,
                actual_sr830_harmonic,
                source_frequency_readback_hz,
            )
            voltage_scale, voltage_unit = select_voltage_display_unit(
                iv_mean_voltages
            )
            status_text.set_text(
                f"Completed {point_index}/{len(currents_rms_ma)} | "
                f"6221 {source_frequency_readback_hz:.9g} Hz | "
                f"SR830 {actual_sr830_harmonic}ω | "
                f"mean {VOLTAGE_CHANNEL}="
                f"{mean_voltage * voltage_scale:+.6g} {voltage_unit}"
            )
            figure.canvas.draw_idle()
            figure.savefig(figure_path, dpi=180)

            print(
                f"  Mean {VOLTAGE_CHANNEL} = {mean_voltage:+.6e} V, "
                f"std = {std_voltage:.3e} V, "
                f"n = {len(formal_samples)}, "
                f"achieved sampling = "
                f"{achieved_sampling_frequency_hz:.3g} Hz"
            )

        completed = not state["stop_requested"]
        if completed:
            print("\nI-V sweep completed.")
            status_text.set_text("Sweep completed; instruments safely stopped.")
            figure.canvas.draw_idle()
            figure.savefig(figure_path, dpi=180)
        else:
            print("\nI-V sweep stopped by user.")

    finally:
        shutdown_instruments()
        raw_file.close()
        summary_file.close()

    print(f"Raw data: {raw_path}")
    print(f"Summary:  {summary_path}")
    if figure_path.exists():
        print(f"I-V plot: {figure_path}")

    if completed and figure is not None and plt.fignum_exists(figure.number):
        plt.ioff()
        plt.show()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by Ctrl+C.")
