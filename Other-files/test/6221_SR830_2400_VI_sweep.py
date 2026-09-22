"""Keithley 2400 voltage pulses with 6221 AC read current and SR830 readout.

The measurement is deliberately split into mutually exclusive phases. Before
every voltage pulse, the most recent 2400 resistance result must be finite and
strictly greater than 20 MOhm; otherwise the scan stops without applying that
pulse and all source outputs are shut down safely:

1. ``2400_VOLTAGE``: SR830 queries are forbidden. The 6221 waveform is off,
   the 2400 applies one voltage pulse, returns to 0 V, and turns its output off.
2. ``IDLE``: the program confirms the 2400 output is off, starts the 6221 AC
   waveform, and waits for the lock-in output to settle without querying SR830.
3. ``SR830_READ``: the 2400 hardware output is confirmed off again, and only
   then may the program query and record SR830 data.

If the SR830 reports overload, the program increases its input range by one
step and records the status with the sample, matching
``6221_SR830_IV_sweep.py``.
Each voltage point uses a 100 s SR830 acquisition window. After that window,
the 6221 is stopped and confirmed off; the 2400 then enters a separate
``2400_RESISTANCE`` phase, auto-ranges a current-source resistance reading,
returns to 0/OFF, and restores voltage-pulse mode. No SR830 query is permitted
during this resistance phase.

The settings GUI first accepts the 6221 RMS current and frequency, SR830
harmonic, and 2400 start voltage, stop voltage, and positive step magnitude.
After Start Formal Recording is clicked, the user selects the output folder.
The voltage path runs from start to stop and then returns to start. The live
window shows
the current 6221 RMS-current readback in the top-right panel, the 2400
resistance in the middle-right panel, and the SR830 voltage summary in the
bottom-right panel. Because some
Keithley 2400 firmware times out on ``:READ?`` while output is off, the SR830
phase uses only ``:OUTP?`` to confirm the 2400 hardware output is disabled.

The SR830 continuously performs internal DSP and has no general SCPI command
that stops its internal demodulator. "Stopped" here means Python sends no SR830
measurement queries and records no SR830 samples while the 2400 applies voltage.

Closing the plot, pressing Ctrl+C, or raising an exception sets the 2400 source
to 0 V and output off, aborts the 6221 waveform and turns its output off, stops
all SR830 queries, and closes every VISA resource.

Each run creates a new ``<source_stem>_Results_<timestamp>`` directory inside
the selected folder and stores a raw CSV, a summary CSV, and the live-plot PNG
there. The CSV layout, numeric formatting, sampling metadata, and incremental
figure saving follow ``6221_SR830_IV_sweep.py``; 2400 pulse and resistance
fields are appended.
"""

import csv
import math
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import matplotlib.pyplot as plt
import numpy as np
import pyvisa


# ----------------------------- VISA addresses -----------------------------
KEITHLEY_2400_ADDRESS = "GPIB0::24::INSTR"
KEITHLEY_6221_ADDRESS = "GPIB0::30::INSTR"
SR830_ADDRESS = "GPIB0::8::INSTR"


# --------------------------- Voltage-pulse scan ---------------------------
# MAX_PULSE_VOLTAGE_V is a hard software safety limit for GUI entries.
MAX_PULSE_VOLTAGE_V = 20.0
START_PULSE_VOLTAGE_V = 20.0
STOP_PULSE_VOLTAGE_V = -20.0
VOLTAGE_STEP_V = 4.0
MAX_VOLTAGE_SCAN_POINTS = 10000
PULSE_WIDTH_S = 0.1
POST_PULSE_SETTLE_S = 0.5
KEITHLEY_2400_CURRENT_COMPLIANCE_A = 1e-3


# -------------------------- AC readout settings ---------------------------
AC_CURRENT_RMS_A = 1e-3
AC_FREQUENCY_HZ = 13.7
KEITHLEY_6221_COMPLIANCE_V = 50.0
KEITHLEY_6221_MARKER_PHASE_DEG = 0.0
KEITHLEY_6221_MARKER_LINE = 4
SR830_HARMONIC = 2
READ_TIME_PER_PULSE_S = 100.0
SAMPLE_INTERVAL_S = 1.0
SR830_SETTLE_AFTER_6221_S = 10.0


# ----------------------------- SR830 setup --------------------------------
# Match 6221_SR830_IV_sweep.py.
SR830_INPUT_MODE = 1  # 0=A, 1=A-B, 2=I 1 MOhm, 3=I 100 MOhm
SR830_SENSITIVITY = 9  # 2 uV full scale in voltage-input mode
SR830_MAX_SENSITIVITY_INDEX = 26  # 1 V full scale
SR830_TIME_CONSTANT = 10  # 1 s
SR830_FILTER_SLOPE = 1  # 12 dB/oct
SR830_MIN_HARMONIC = 1
SR830_MAX_HARMONIC = 19999
SR830_MAX_DEMOD_FREQUENCY_HZ = 102000.0


# ---------------------- 2400 resistance readback -------------------------
RES_CURRENT_LEVELS_A = (
    1e-9,
    3e-9,
    1e-8,
    3e-8,
    1e-7,
    3e-7,
    1e-6,
    3e-6,
    1e-5,
    3e-5,
    1e-4,
    3e-4,
    1e-3,
)
RES_TARGET_VOLTAGE_LOW_V = 0.05
RES_TARGET_VOLTAGE_HIGH_V = 2.0
RES_VOLTAGE_COMPLIANCE_V = 5.0
RES_SETTLE_TIME_S = 0.2
RES_AVERAGE_COUNT = 5
RES_NPLC = 1.0
MIN_RESISTANCE_FOR_PULSE_OHM = 20e6


# ------------------------------ Data output -------------------------------
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
FORMAL_MEASUREMENT_STAGE = "voltage_pulse_readout"
RAW_CSV_HEADER = (
    "timestamp",
    "measurement_stage",
    "point_index",
    "pulse_voltage_setpoint_V",
    "current_readback_RMS_mA",
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
    "SR830_sensitivity_index_after_sample",
    "SR830_auto_range_action",
    "2400_output_state",
    "pulse_gate_resistance_Ohm",
)
SUMMARY_CSV_HEADER = (
    "point_index",
    "pulse_voltage_setpoint_V",
    "current_readback_RMS_mA",
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
    "pulse_gate_resistance_Ohm",
    "2400_resistance_source_current_A",
    "2400_resistance_voltage_V",
    "2400_resistance_current_A",
    "2400_resistance_Ohm",
)


class MeasurementStopped(Exception):
    """Internal signal raised when the live plot is closed."""


class ResistanceGateBlocked(Exception):
    """Stop safely when resistance does not permit a voltage pulse."""


class MeasurementInterlock:
    """Prevent SR830 reads and 2400 voltage output from overlapping."""

    IDLE = "IDLE"
    VOLTAGE = "2400_VOLTAGE"
    RESISTANCE = "2400_RESISTANCE"
    SR830_READ = "SR830_READ"

    def __init__(self):
        self.phase = self.IDLE

    def begin_voltage(self):
        if self.phase != self.IDLE:
            raise RuntimeError(
                f"Cannot start 2400 voltage while phase is {self.phase}."
            )
        self.phase = self.VOLTAGE

    def end_voltage(self):
        if self.phase == self.VOLTAGE:
            self.phase = self.IDLE

    def begin_sr830_read(self):
        if self.phase != self.IDLE:
            raise RuntimeError(
                f"Cannot start SR830 reading while phase is {self.phase}."
            )
        self.phase = self.SR830_READ

    def begin_resistance(self):
        if self.phase != self.IDLE:
            raise RuntimeError(
                f"Cannot start 2400 resistance while phase is {self.phase}."
            )
        self.phase = self.RESISTANCE

    def end_resistance(self):
        if self.phase == self.RESISTANCE:
            self.phase = self.IDLE

    def end_sr830_read(self):
        if self.phase == self.SR830_READ:
            self.phase = self.IDLE

    def assert_voltage_allowed(self):
        if self.phase != self.VOLTAGE:
            raise RuntimeError(
                "2400 voltage command rejected by measurement interlock."
            )

    def assert_sr830_read_allowed(self):
        if self.phase != self.SR830_READ:
            raise RuntimeError(
                "SR830 query blocked: 2400 voltage/settling phase is active."
            )

    def force_idle(self):
        self.phase = self.IDLE


class LivePlot:
    def __init__(
        self,
        sr830_harmonic,
        source_frequency_hz,
        source_current_rms_a,
        close_callback=None,
    ):
        plt.ion()
        self.stop_requested = False
        self.close_callback = close_callback

        self.figure, axes = plt.subplots(3, 2, figsize=(13, 9))
        self.figure.suptitle(
            "Keithley 2400 + 6221 + SR830 measurement  |  "
            f"6221 {source_current_rms_a * 1e3:.9g} mA RMS, "
            f"{source_frequency_hz:.9g} Hz  |  "
            f"SR830 {sr830_harmonic}ω",
            y=0.975,
        )
        self.figure.canvas.mpl_connect("close_event", self._on_close)

        (
            self.pulse_axis,
            self.current_axis,
            self.r_axis,
            self.resistance_axis,
            self.theta_axis,
            self.summary_axis,
        ) = axes.flat

        self.pulse_line, = self.pulse_axis.plot([], [], "-", lw=1.5)
        self.r_line, = self.r_axis.plot([], [], "o-", ms=3)
        self.theta_line, = self.theta_axis.plot([], [], "o-", ms=3)
        self.current_line, = self.current_axis.plot(
            [], [], "o-", ms=3, color="tab:green"
        )
        self.summary_line, = self.summary_axis.plot([], [], "o-", ms=4)
        self.resistance_line, = self.resistance_axis.plot(
            [],
            [],
            "s-",
            ms=4,
            color="tab:purple",
        )

        self.pulse_axis.set_title("2400 command voltage")
        self.pulse_axis.set_xlabel("Global time (s)")
        self.pulse_axis.set_ylabel("Voltage (V)")

        self.r_axis.set_title(f"SR830 R at {sr830_harmonic}ω")
        self.r_axis.set_xlabel("Global time (s)")
        self.r_axis.set_ylabel("R (V)")

        self.theta_axis.set_title(
            f"SR830 theta at {sr830_harmonic}ω"
        )
        self.theta_axis.set_xlabel("Global time (s)")
        self.theta_axis.set_ylabel("Theta (deg)")

        self.current_axis.set_title("6221 current readback")
        self.current_axis.set_xlabel("Global time (s)")
        self.current_axis.set_ylabel("Current (mA RMS)")

        self.summary_axis.set_title("Mean SR830 R vs pulse voltage")
        self.summary_axis.set_xlabel("2400 pulse voltage (V)")
        self.summary_axis.set_ylabel("Mean R (V)")

        self.resistance_axis.set_title("2400 resistance vs pulse voltage")
        self.resistance_axis.set_xlabel("2400 pulse voltage (V)")
        self.resistance_axis.set_ylabel("Resistance (Ohm)")

        for axis in axes.flat:
            axis.grid(True)
            axis.margins(x=0.05, y=0.10)

        self.pulse_times = []
        self.pulse_voltages = []
        self.read_times = []
        self.current_times = []
        self.r_values = []
        self.theta_values = []
        self.current_readbacks_rms_ma = []
        self.summary_voltages = []
        self.summary_r_values = []
        self.resistance_values = []
        self.current_read_pulse_index = None
        self.current_read_sample_count = 0

        self.status_text = self.figure.text(0.02, 0.01, "Ready")
        self.figure.subplots_adjust(
            left=0.08,
            right=0.97,
            bottom=0.11,
            top=0.88,
            hspace=0.58,
            wspace=0.30,
        )
        self.figure.canvas.draw()
        self.figure.canvas.flush_events()

    def _on_close(self, _event):
        if self.stop_requested:
            return
        self.stop_requested = True
        if self.close_callback is not None:
            self.close_callback()

    def ensure_open(self):
        if self.stop_requested or not plt.fignum_exists(self.figure.number):
            self.stop_requested = True
            raise MeasurementStopped("Live plot closed by user.")

    @staticmethod
    def _autoscale(axis):
        axis.relim()
        axis.autoscale_view()

    def _draw(self, status):
        self.ensure_open()
        self.status_text.set_text(status)
        self.figure.canvas.draw_idle()
        self.figure.canvas.flush_events()
        self.ensure_open()

    def process_events(self):
        """Keep this figure responsive without redrawing unchanged data."""
        self.ensure_open()
        self.figure.canvas.flush_events()
        self.ensure_open()

    def add_pulse_point(self, global_time_s, voltage_v, status):
        self.pulse_times.append(global_time_s)
        self.pulse_voltages.append(voltage_v)
        self.pulse_line.set_data(self.pulse_times, self.pulse_voltages)
        self._autoscale(self.pulse_axis)
        self._draw(status)

    def set_status(self, status):
        self._draw(status)

    def add_current_point(self, global_time_s, current_rms_ma, status):
        self.current_times.append(global_time_s)
        self.current_readbacks_rms_ma.append(current_rms_ma)
        self.current_line.set_data(
            self.current_times, self.current_readbacks_rms_ma
        )
        self._autoscale(self.current_axis)
        self._draw(status)

    def add_read_point(self, row):
        if row["pulse_index"] != self.current_read_pulse_index:
            self.current_read_pulse_index = row["pulse_index"]
            self.current_read_sample_count = 0
        self.current_read_sample_count += 1

        self.read_times.append(row["global_time_s"])
        self.current_times.append(row["global_time_s"])
        self.r_values.append(row["sr830_r_v"])
        self.theta_values.append(row["sr830_theta_deg"])
        self.current_readbacks_rms_ma.append(
            row["k6221_i_rms_readback_a"] * 1e3
        )

        voltage_scale, voltage_unit = select_voltage_display_unit(
            self.r_values
        )
        self.r_line.set_data(
            self.read_times,
            [value * voltage_scale for value in self.r_values],
        )
        self.r_axis.set_ylabel(f"R ({voltage_unit})")
        self.theta_line.set_data(self.read_times, self.theta_values)
        self.current_line.set_data(
            self.current_times, self.current_readbacks_rms_ma
        )
        for axis in (
            self.r_axis,
            self.theta_axis,
            self.current_axis,
        ):
            self._autoscale(axis)
        self._draw(
            f"SR830 READ | pulse {row['pulse_index']} | "
            f"R={row['sr830_r_v'] * voltage_scale:+.6g} "
            f"{voltage_unit} | "
            f"{self.current_read_sample_count} samples | "
            f"{row['read_time_s']:.1f}/{READ_TIME_PER_PULSE_S:.1f} s"
        )

    def add_summary_point(self, voltage_v, mean_r_v, resistance_ohm):
        self.summary_voltages.append(voltage_v)
        self.summary_r_values.append(mean_r_v)
        self.resistance_values.append(resistance_ohm)
        voltage_scale, voltage_unit = select_voltage_display_unit(
            self.summary_r_values
        )
        self.summary_line.set_data(
            self.summary_voltages,
            [value * voltage_scale for value in self.summary_r_values],
        )
        self.summary_axis.set_ylabel(f"Mean R ({voltage_unit})")
        self.resistance_line.set_data(
            self.summary_voltages,
            self.resistance_values,
        )
        self._autoscale(self.summary_axis)
        self._autoscale(self.resistance_axis)
        self._draw(
            f"Completed pulse {voltage_v:+.6g} V | "
            f"mean SR830 R={mean_r_v * voltage_scale:+.6g} "
            f"{voltage_unit} | "
            f"2400 resistance={resistance_ohm:+.6e} Ohm"
        )


def rms_to_peak(current_rms_a):
    return math.sqrt(2.0) * current_rms_a


def normalize_sr830_harmonic(value):
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise ValueError("SR830 harmonic must be a finite number.")
    return int(math.floor(abs(numeric_value) + 0.5))


def ask_output_parent_directory():
    """Ask where this run's timestamped results folder should be created."""
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    try:
        selected = filedialog.askdirectory(
            parent=root,
            title=(
                "Choose folder for 6221–SR830–2400 measurement results"
            ),
            initialdir=str(Path(__file__).resolve().parent),
            mustexist=True,
        )
    finally:
        root.destroy()
    if not selected:
        return None
    return Path(selected).expanduser().resolve()


def ask_readout_settings():
    """Collect source, lock-in, and voltage-scan settings before VISA."""
    root = tk.Tk()
    root.title("6221–SR830–2400 V–I sweep settings")
    root.resizable(False, False)

    frequency_var = tk.StringVar(value=f"{AC_FREQUENCY_HZ:g}")
    harmonic_var = tk.StringVar(value=str(SR830_HARMONIC))
    current_rms_ma_var = tk.StringVar(
        value=f"{AC_CURRENT_RMS_A * 1e3:g}"
    )
    start_voltage_var = tk.StringVar(
        value=f"{START_PULSE_VOLTAGE_V:g}"
    )
    stop_voltage_var = tk.StringVar(
        value=f"{STOP_PULSE_VOLTAGE_V:g}"
    )
    voltage_step_var = tk.StringVar(value=f"{VOLTAGE_STEP_V:g}")
    result = {"values": None}

    frame = ttk.Frame(root, padding=16)
    frame.grid(row=0, column=0)
    ttk.Label(
        frame,
        text="Three-instrument readout settings",
        font=("", 11, "bold"),
    ).grid(row=0, column=0, columnspan=3, pady=(0, 12))

    ttk.Label(frame, text="6221 current").grid(
        row=1, column=0, sticky="w", pady=4
    )
    ttk.Entry(
        frame,
        textvariable=current_rms_ma_var,
        width=14,
        justify="right",
    ).grid(row=1, column=1, padx=(12, 5), pady=4)
    ttk.Label(frame, text="mA RMS").grid(
        row=1, column=2, sticky="w", pady=4
    )

    ttk.Label(frame, text="6221 frequency").grid(
        row=2, column=0, sticky="w", pady=4
    )
    ttk.Entry(
        frame,
        textvariable=frequency_var,
        width=14,
        justify="right",
    ).grid(row=2, column=1, padx=(12, 5), pady=4)
    ttk.Label(frame, text="Hz").grid(
        row=2, column=2, sticky="w", pady=4
    )

    ttk.Label(frame, text="SR830 harmonic").grid(
        row=3, column=0, sticky="w", pady=4
    )
    harmonic_entry = ttk.Entry(
        frame,
        textvariable=harmonic_var,
        width=14,
        justify="right",
    )
    harmonic_entry.grid(row=3, column=1, padx=(12, 5), pady=4)
    ttk.Label(frame, text="× reference").grid(
        row=3, column=2, sticky="w", pady=4
    )

    voltage_fields = (
        ("2400 start voltage", start_voltage_var, "V"),
        ("2400 stop voltage", stop_voltage_var, "V"),
        ("2400 step magnitude", voltage_step_var, "V"),
    )
    for row, (label, variable, unit) in enumerate(
        voltage_fields, start=4
    ):
        ttk.Label(frame, text=label).grid(
            row=row, column=0, sticky="w", pady=4
        )
        ttk.Entry(
            frame,
            textvariable=variable,
            width=14,
            justify="right",
        ).grid(row=row, column=1, padx=(12, 5), pady=4)
        ttk.Label(frame, text=unit).grid(
            row=row, column=2, sticky="w", pady=4
        )

    ttk.Label(
        frame,
        text=(
            f"2400 address: {KEITHLEY_2400_ADDRESS}\n"
            "Voltage scan order: start → stop → start\n"
            f"Software absolute-voltage limit: "
            f"{MAX_PULSE_VOLTAGE_V:g} V\n"
            "Pulse resistance gate: measured resistance must be "
            f"> {MIN_RESISTANCE_FOR_PULSE_OHM / 1e6:g} MOhm"
        ),
        foreground="#555555",
        justify="left",
    ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(10, 12))

    def parsed_values():
        current_rms_a = float(current_rms_ma_var.get()) * 1e-3
        frequency_hz = float(frequency_var.get())
        harmonic = normalize_sr830_harmonic(harmonic_var.get())
        start_voltage_v = float(start_voltage_var.get())
        stop_voltage_v = float(stop_voltage_var.get())
        voltage_step_v = float(voltage_step_var.get())
        validate_settings(
            frequency_hz,
            harmonic,
            current_rms_a,
            start_voltage_v,
            stop_voltage_v,
            voltage_step_v,
        )
        return (
            frequency_hz,
            harmonic,
            current_rms_a,
            start_voltage_v,
            stop_voltage_v,
            voltage_step_v,
        )

    def normalize_harmonic_entry(_event=None):
        try:
            harmonic_var.set(
                str(normalize_sr830_harmonic(harmonic_var.get()))
            )
        except (TypeError, ValueError):
            pass

    def start_measurement():
        try:
            values = parsed_values()
            harmonic_var.set(str(values[1]))
        except Exception as exc:
            messagebox.showerror("Invalid measurement settings", str(exc))
            return
        result["values"] = values
        root.destroy()

    def cancel():
        root.destroy()

    button_frame = ttk.Frame(frame)
    button_frame.grid(row=8, column=0, columnspan=3)
    ttk.Button(
        button_frame,
        text="Start Formal Recording",
        command=start_measurement,
    ).grid(row=0, column=0, padx=5)
    ttk.Button(
        button_frame,
        text="Cancel",
        command=cancel,
    ).grid(row=0, column=1, padx=5)

    harmonic_entry.bind("<FocusOut>", normalize_harmonic_entry)
    root.protocol("WM_DELETE_WINDOW", cancel)
    root.bind("<Return>", lambda _event: start_measurement())
    root.mainloop()
    return result["values"]


def query_text(instrument, command):
    return instrument.query(command).strip()


def query_float(instrument, command):
    return float(query_text(instrument, command))


def assert_no_keithley_error(instrument, model, context):
    response = query_text(instrument, "SYST:ERR?")
    error_code = int(response.split(",", 1)[0].strip())
    if error_code != 0:
        raise RuntimeError(
            f"{model} SCPI error after {context}: {response}"
        )


def write_keithley_checked(instrument, model, command):
    instrument.write(command)
    time.sleep(0.03)
    assert_no_keithley_error(instrument, model, repr(command))


def write_sr830_checked(sr830, command):
    sr830.write(command)
    event_status = int(query_text(sr830, "*ESR?"))
    if event_status & 0x30:
        raise RuntimeError(
            f"SR830 rejected {command!r}; *ESR? returned {event_status}"
        )


def verify_identity(instrument, expected_model, address):
    identity = query_text(instrument, "*IDN?")
    if expected_model.upper() not in identity.upper():
        raise RuntimeError(
            f"Unexpected instrument at {address}. Expected "
            f"{expected_model}, received: {identity}"
        )
    return identity


def validate_settings(
    ac_frequency_hz=AC_FREQUENCY_HZ,
    sr830_harmonic=SR830_HARMONIC,
    ac_current_rms_a=AC_CURRENT_RMS_A,
    start_voltage_v=START_PULSE_VOLTAGE_V,
    stop_voltage_v=STOP_PULSE_VOLTAGE_V,
    voltage_step_v=VOLTAGE_STEP_V,
):
    sr830_harmonic = normalize_sr830_harmonic(sr830_harmonic)
    if MAX_PULSE_VOLTAGE_V <= 0:
        raise ValueError("MAX_PULSE_VOLTAGE_V must be > 0.")
    if not all(
        math.isfinite(value)
        for value in (
            start_voltage_v,
            stop_voltage_v,
            voltage_step_v,
        )
    ):
        raise ValueError("2400 voltage scan values must be finite.")
    if voltage_step_v <= 0:
        raise ValueError("2400 voltage step magnitude must be > 0.")
    if max(abs(start_voltage_v), abs(stop_voltage_v)) > (
        MAX_PULSE_VOLTAGE_V
    ):
        raise ValueError(
            f"2400 start/stop voltage must stay within "
            f"±{MAX_PULSE_VOLTAGE_V:g} V."
        )
    one_way_intervals = (
        abs(stop_voltage_v - start_voltage_v) / voltage_step_v
    )
    estimated_scan_points = int(math.ceil(one_way_intervals)) * 2 + 1
    if estimated_scan_points > MAX_VOLTAGE_SCAN_POINTS:
        raise ValueError(
            f"Voltage scan would contain about "
            f"{estimated_scan_points} points; limit is "
            f"{MAX_VOLTAGE_SCAN_POINTS}."
        )
    if PULSE_WIDTH_S <= 0:
        raise ValueError("PULSE_WIDTH_S must be > 0.")
    if POST_PULSE_SETTLE_S < 0:
        raise ValueError("POST_PULSE_SETTLE_S must be >= 0.")
    if READ_TIME_PER_PULSE_S <= 0:
        raise ValueError("READ_TIME_PER_PULSE_S must be > 0.")
    if SAMPLE_INTERVAL_S <= 0:
        raise ValueError("SAMPLE_INTERVAL_S must be > 0.")
    if SR830_SETTLE_AFTER_6221_S < 0:
        raise ValueError("SR830_SETTLE_AFTER_6221_S must be >= 0.")
    if not 0 < KEITHLEY_2400_CURRENT_COMPLIANCE_A <= 1.0:
        raise ValueError("Invalid 2400 current compliance.")
    if not 0 < KEITHLEY_6221_COMPLIANCE_V <= 105:
        raise ValueError("Invalid 6221 compliance.")
    if not RES_CURRENT_LEVELS_A:
        raise ValueError("RES_CURRENT_LEVELS_A cannot be empty.")
    if any(
        not math.isfinite(level) or not 0 < level <= 1.0
        for level in RES_CURRENT_LEVELS_A
    ):
        raise ValueError("Invalid 2400 resistance source-current level.")
    if (
        tuple(sorted(RES_CURRENT_LEVELS_A)) != RES_CURRENT_LEVELS_A
        or len(set(RES_CURRENT_LEVELS_A)) != len(RES_CURRENT_LEVELS_A)
    ):
        raise ValueError(
            "RES_CURRENT_LEVELS_A must be strictly ascending."
        )
    if not 0 < RES_TARGET_VOLTAGE_LOW_V < (
        RES_TARGET_VOLTAGE_HIGH_V
    ) < RES_VOLTAGE_COMPLIANCE_V:
        raise ValueError("Invalid 2400 resistance voltage targets.")
    if RES_SETTLE_TIME_S < 0 or RES_AVERAGE_COUNT < 1:
        raise ValueError("Invalid 2400 resistance averaging settings.")
    if RES_NPLC <= 0:
        raise ValueError("RES_NPLC must be > 0.")
    if (
        not math.isfinite(MIN_RESISTANCE_FOR_PULSE_OHM)
        or MIN_RESISTANCE_FOR_PULSE_OHM <= 0
    ):
        raise ValueError(
            "MIN_RESISTANCE_FOR_PULSE_OHM must be positive and finite."
        )
    if not math.isfinite(ac_current_rms_a) or ac_current_rms_a <= 0:
        raise ValueError(
            "6221 RMS current must be a positive finite value."
        )
    peak_current_a = rms_to_peak(ac_current_rms_a)
    if not 0 < peak_current_a <= 0.105:
        maximum_rms_ma = 0.105 / math.sqrt(2.0) * 1e3
        raise ValueError(
            f"6221 current exceeds the waveform limit of "
            f"{maximum_rms_ma:.6g} mA RMS."
        )
    if not math.isfinite(ac_frequency_hz) or ac_frequency_hz <= 0:
        raise ValueError("6221 frequency must be a positive finite value.")
    if not SR830_MIN_HARMONIC <= sr830_harmonic <= SR830_MAX_HARMONIC:
        raise ValueError(
            f"SR830 harmonic must round to an integer within "
            f"{SR830_MIN_HARMONIC}–{SR830_MAX_HARMONIC}."
        )
    demod_frequency_hz = ac_frequency_hz * sr830_harmonic
    if demod_frequency_hz > SR830_MAX_DEMOD_FREQUENCY_HZ:
        raise ValueError(
            f"SR830 demodulation frequency would be "
            f"{demod_frequency_hz:g} Hz, above its "
            f"{SR830_MAX_DEMOD_FREQUENCY_HZ:g} Hz limit."
        )


def build_scan_segment(start, stop, step):
    if step <= 0:
        raise ValueError("Voltage step must be > 0.")
    values = []
    direction = 1.0 if stop >= start else -1.0
    value = float(start)
    tolerance = step * 1e-9
    while (
        value <= stop + tolerance
        if direction > 0
        else value >= stop - tolerance
    ):
        values.append(round(value, 12))
        value += direction * step
    if not values or not math.isclose(
        values[-1], stop, abs_tol=tolerance
    ):
        values.append(float(stop))
    else:
        values[-1] = float(stop)
    return values


def build_voltage_scan(
    start_voltage_v=START_PULSE_VOLTAGE_V,
    stop_voltage_v=STOP_PULSE_VOLTAGE_V,
    voltage_step_v=VOLTAGE_STEP_V,
):
    outward = build_scan_segment(
        start_voltage_v,
        stop_voltage_v,
        voltage_step_v,
    )
    return_path = build_scan_segment(
        stop_voltage_v,
        start_voltage_v,
        voltage_step_v,
    )
    return outward + return_path[1:]


def safe_shutdown_2400(k2400, reset=True):
    errors = []
    commands = [":SOUR:VOLT 0", ":SOUR:CURR 0", ":OUTP OFF"]
    if reset:
        commands.extend(("*RST", "*CLS"))
    for command in commands:
        try:
            k2400.write(command)
        except Exception as exc:
            errors.append(f"{command}: {exc}")
    if errors:
        raise RuntimeError("2400 shutdown failed: " + "; ".join(errors))


def stop_6221(k6221, reset=False):
    errors = []
    commands = [":SOUR:WAVE:ABOR", ":OUTP OFF"]
    if reset:
        commands[1:1] = [":CURR 0"]
        commands.extend(("*RST", "*CLS"))
    for command in commands:
        try:
            k6221.write(command)
        except Exception as exc:
            errors.append(f"{command}: {exc}")
    if errors:
        raise RuntimeError("6221 shutdown failed: " + "; ".join(errors))


def confirm_2400_output_off(k2400, context):
    """Require hardware output readback OFF before any SR830 query."""
    output_state = int(query_float(k2400, ":OUTP?"))
    if output_state != 0:
        safe_shutdown_2400(k2400, reset=False)
        output_state = int(query_float(k2400, ":OUTP?"))
    if output_state != 0:
        raise RuntimeError(
            f"2400 output is still ON during {context}; "
            "SR830 reading is blocked."
        )
    assert_no_keithley_error(k2400, "2400", context)


def confirm_6221_output_off(k6221, context):
    """Require waveform abort and hardware output readback OFF."""
    output_state = int(query_float(k6221, ":OUTP?"))
    if output_state != 0:
        stop_6221(k6221, reset=False)
        output_state = int(query_float(k6221, ":OUTP?"))
    if output_state != 0:
        raise RuntimeError(
            f"6221 output is still ON during {context}; "
            "2400 voltage output is blocked."
        )
    assert_no_keithley_error(k6221, "6221", context)


def connect_instruments():
    resource_manager = pyvisa.ResourceManager()
    opened = []
    try:
        for address in (
            KEITHLEY_2400_ADDRESS,
            KEITHLEY_6221_ADDRESS,
            SR830_ADDRESS,
        ):
            instrument = resource_manager.open_resource(address)
            opened.append(instrument)
            instrument.timeout = 10000
            instrument.write_termination = "\n"
            instrument.read_termination = "\n"

        k2400, k6221, sr830 = opened
        sr830.write("OUTX 1")
        identities = (
            verify_identity(k2400, "2400", KEITHLEY_2400_ADDRESS),
            verify_identity(k6221, "6221", KEITHLEY_6221_ADDRESS),
            verify_identity(sr830, "SR830", SR830_ADDRESS),
        )
        for identity in identities:
            print("Connected:", identity)
        return resource_manager, k2400, k6221, sr830
    except BaseException:
        if len(opened) >= 1:
            try:
                safe_shutdown_2400(opened[0], reset=False)
            except Exception:
                pass
        if len(opened) >= 2:
            try:
                stop_6221(opened[1], reset=False)
            except Exception:
                pass
        for instrument in reversed(opened):
            try:
                instrument.close()
            except Exception:
                pass
        resource_manager.close()
        raise


def setup_2400(k2400):
    k2400.write("*RST")
    k2400.write("*CLS")
    for command in (
        ":SOUR:FUNC VOLT",
        ":SOUR:VOLT:MODE FIXED",
        ":SOUR:VOLT 0",
        ":SENS:FUNC 'CURR'",
        f":SENS:CURR:PROT {KEITHLEY_2400_CURRENT_COMPLIANCE_A:.12g}",
        ":SENS:CURR:RANG:AUTO ON",
        ":FORM:ELEM VOLT,CURR",
        ":OUTP OFF",
    ):
        write_keithley_checked(k2400, "2400", command)


def setup_2400_resistance(k2400, source_current_a):
    k2400.write("*RST")
    k2400.write("*CLS")
    for command in (
        ":SOUR:FUNC CURR",
        ":SOUR:CURR:MODE FIXED",
        ":SOUR:CURR 0",
        f":SENS:VOLT:PROT {RES_VOLTAGE_COMPLIANCE_V:.12g}",
        ":SENS:FUNC:CONC ON",
        ":SENS:FUNC 'VOLT','CURR'",
        ":SENS:VOLT:RANG:AUTO ON",
        ":SENS:CURR:RANG:AUTO ON",
        f":SENS:VOLT:NPLC {RES_NPLC:.12g}",
        f":SENS:CURR:NPLC {RES_NPLC:.12g}",
        ":FORM:ELEM VOLT,CURR",
        ":OUTP ON",
        f":SOUR:CURR {source_current_a:.12e}",
    ):
        write_keithley_checked(k2400, "2400", command)
    if int(query_float(k2400, ":OUTP?")) != 1:
        raise RuntimeError("2400 output did not turn on for resistance.")


def set_2400_resistance_current(k2400, source_current_a):
    write_keithley_checked(
        k2400,
        "2400",
        f":SOUR:CURR {source_current_a:.12e}",
    )


def read_2400_resistance_sample(k2400):
    if int(query_float(k2400, ":OUTP?")) != 1:
        raise RuntimeError(
            "2400 output turned off during resistance measurement."
        )
    response = query_text(k2400, ":READ?")
    values = [float(value) for value in response.split(",")]
    if len(values) < 2:
        raise RuntimeError(
            f"Unexpected 2400 resistance response: {response}"
        )
    voltage_v, current_a = values[:2]
    if not math.isfinite(voltage_v) or not math.isfinite(current_a):
        raise RuntimeError(
            f"Non-finite 2400 resistance response: {response}"
        )
    if abs(current_a) < 1e-15:
        raise RuntimeError(
            "2400 measured current is too small to calculate resistance."
        )
    return voltage_v, current_a, voltage_v / current_a


def measure_2400_resistance(k2400, k6221, interlock, live_plot):
    """Auto-range one resistance reading with all SR830 queries blocked."""
    confirm_6221_output_off(
        k6221, "before 2400 resistance measurement"
    )
    interlock.begin_resistance()
    cleanup_errors = []
    try:
        current_index = 0
        source_current_a = RES_CURRENT_LEVELS_A[current_index]
        setup_2400_resistance(k2400, source_current_a)

        voltage_v = math.nan
        current_a = math.nan
        resistance_ohm = math.nan
        for _ in range(len(RES_CURRENT_LEVELS_A) * 2):
            live_plot.set_status(
                f"2400 measuring resistance | "
                f"source {source_current_a:.3e} A | "
                "SR830 queries blocked"
            )
            interruptible_wait(RES_SETTLE_TIME_S, live_plot)
            (
                voltage_v,
                current_a,
                resistance_ohm,
            ) = read_2400_resistance_sample(k2400)
            abs_voltage_v = abs(voltage_v)

            if (
                abs_voltage_v >= RES_VOLTAGE_COMPLIANCE_V * 0.98
                and current_index == 0
            ):
                raise RuntimeError(
                    "Resistance is above the measurable range at the "
                    "minimum 2400 test current."
                )
            if (
                abs_voltage_v > RES_TARGET_VOLTAGE_HIGH_V
                and current_index > 0
            ):
                current_index -= 1
            elif (
                abs_voltage_v < RES_TARGET_VOLTAGE_LOW_V
                and current_index < len(RES_CURRENT_LEVELS_A) - 1
            ):
                current_index += 1
            else:
                break

            source_current_a = RES_CURRENT_LEVELS_A[current_index]
            set_2400_resistance_current(k2400, source_current_a)

        readings = []
        for _ in range(RES_AVERAGE_COUNT):
            interruptible_wait(RES_SETTLE_TIME_S, live_plot)
            readings.append(read_2400_resistance_sample(k2400))
        if not readings:
            raise RuntimeError("No valid 2400 resistance readings.")

        voltage_v = float(np.mean([item[0] for item in readings]))
        current_a = float(np.mean([item[1] for item in readings]))
        resistance_ohm = float(np.mean([item[2] for item in readings]))
        return {
            "k2400_resistance_source_current_a": source_current_a,
            "k2400_resistance_voltage_v": voltage_v,
            "k2400_resistance_current_a": current_a,
            "k2400_resistance_ohm": resistance_ohm,
        }
    finally:
        try:
            safe_shutdown_2400(k2400, reset=False)
        except Exception as exc:
            cleanup_errors.append(str(exc))
        try:
            confirm_2400_output_off(
                k2400, "after resistance measurement"
            )
        except Exception as exc:
            cleanup_errors.append(str(exc))
        try:
            setup_2400(k2400)
            confirm_2400_output_off(
                k2400, "after restoring voltage-pulse mode"
            )
        except Exception as exc:
            cleanup_errors.append(str(exc))
        interlock.end_resistance()
        if cleanup_errors:
            raise RuntimeError(
                "2400 resistance cleanup failed: "
                + "; ".join(cleanup_errors)
            )


def require_resistance_gate_for_pulse(
    resistance_result,
    pulse_index,
    voltage_v,
):
    """Return the gate resistance or stop before any pulse is applied."""
    try:
        resistance_ohm = float(
            resistance_result["k2400_resistance_ohm"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ResistanceGateBlocked(
            f"Pulse {pulse_index} ({voltage_v:+.6g} V) was blocked: "
            "the 2400 resistance result is missing or invalid."
        ) from exc

    if not math.isfinite(resistance_ohm):
        raise ResistanceGateBlocked(
            f"Pulse {pulse_index} ({voltage_v:+.6g} V) was blocked: "
            "the 2400 resistance result is not finite."
        )
    if resistance_ohm <= MIN_RESISTANCE_FOR_PULSE_OHM:
        raise ResistanceGateBlocked(
            f"Pulse {pulse_index} ({voltage_v:+.6g} V) was blocked: "
            f"measured resistance {resistance_ohm:.6e} Ohm is not "
            f"above {MIN_RESISTANCE_FOR_PULSE_OHM:.6e} Ohm."
        )
    return resistance_ohm


def setup_6221(k6221, ac_frequency_hz, ac_current_rms_a):
    current_peak_a = rms_to_peak(ac_current_rms_a)
    k6221.write("*RST")
    k6221.write("*CLS")
    for command in (
        f":CURR:COMP {KEITHLEY_6221_COMPLIANCE_V:.12g}",
        ":SOUR:WAVE:FUNC SIN",
        f":SOUR:WAVE:FREQ {ac_frequency_hz:.12g}",
        f":SOUR:WAVE:AMPL {current_peak_a:.12e}",
        ":SOUR:WAVE:OFFS 0",
        ":SOUR:WAVE:RANG BEST",
        f":SOUR:WAVE:PMAR {KEITHLEY_6221_MARKER_PHASE_DEG:.12g}",
        f":SOUR:WAVE:PMAR:OLIN {KEITHLEY_6221_MARKER_LINE}",
        ":SOUR:WAVE:PMAR:STAT ON",
        ":OUTP OFF",
    ):
        write_keithley_checked(k6221, "6221", command)
    actual_frequency_hz = query_float(k6221, ":SOUR:WAVE:FREQ?")
    if not math.isclose(
        actual_frequency_hz,
        ac_frequency_hz,
        rel_tol=1e-6,
        abs_tol=1e-9,
    ):
        raise RuntimeError(
            "6221 frequency readback mismatch: "
            f"requested {ac_frequency_hz:g} Hz, "
            f"received {actual_frequency_hz:g} Hz."
        )
    actual_peak_current_a = query_float(
        k6221, ":SOUR:WAVE:AMPL?"
    )
    actual_current_rms_a = actual_peak_current_a / math.sqrt(2.0)
    if not math.isclose(
        actual_current_rms_a,
        ac_current_rms_a,
        rel_tol=1e-6,
        abs_tol=1e-12,
    ):
        raise RuntimeError(
            "6221 current readback mismatch: "
            f"requested {ac_current_rms_a * 1e3:g} mA RMS, "
            f"received {actual_current_rms_a * 1e3:g} mA RMS."
        )
    return actual_frequency_hz, actual_current_rms_a


def setup_sr830(sr830, sr830_harmonic):
    sr830.write("*CLS")
    for command in (
        "FMOD 0",
        "RSLP 2",  # 6221 Trigger Link phase marker: TTL falling edge
        f"HARM {sr830_harmonic}",
        f"ISRC {SR830_INPUT_MODE}",
        "IGND 0",
        "ICPL 0",  # AC coupling
        "ILIN 1",
        f"SENS {SR830_SENSITIVITY}",
        f"OFLT {SR830_TIME_CONSTANT}",
        f"OFSL {SR830_FILTER_SLOPE}",
        "RMOD 1",
        "PHAS 0",
        "DDEF 1,1,0",
        "DDEF 2,1,0",
    ):
        write_sr830_checked(sr830, command)

    channel_1_display = tuple(
        int(float(value))
        for value in query_text(sr830, "DDEF? 1").split(",")
    )
    channel_2_display = tuple(
        int(float(value))
        for value in query_text(sr830, "DDEF? 2").split(",")
    )
    if channel_1_display != (1, 0) or channel_2_display != (1, 0):
        raise RuntimeError(
            "SR830 front-panel display verification failed: "
            f"CH1={channel_1_display}, CH2={channel_2_display}"
        )
    reference_mode = int(query_float(sr830, "FMOD?"))
    reference_slope = int(query_float(sr830, "RSLP?"))
    if reference_mode != 0 or reference_slope != 2:
        raise RuntimeError(
            "SR830 external-reference readback mismatch: "
            f"FMOD={reference_mode}, RSLP={reference_slope}"
        )

    harmonic_readback = int(query_float(sr830, "HARM?"))
    if harmonic_readback != sr830_harmonic:
        raise RuntimeError(
            "SR830 harmonic readback mismatch: "
            f"requested {sr830_harmonic}, received {harmonic_readback}"
        )
    reference_frequency_hz = query_float(sr830, "FREQ?")
    print("SR830 front panel: Channel 1 = R, Channel 2 = theta")
    print(f"SR830 harmonic: {harmonic_readback}")
    print(f"SR830 reference frequency: {reference_frequency_hz:.6g} Hz")
    return harmonic_readback


def start_6221(k6221, current_rms_a):
    current_peak_a = rms_to_peak(current_rms_a)
    for command in (
        ":SOUR:WAVE:ABOR",
        f":SOUR:WAVE:AMPL {current_peak_a:.12e}",
        ":SOUR:WAVE:ARM",
        ":SOUR:WAVE:INIT",
    ):
        write_keithley_checked(k6221, "6221", command)
    if int(query_float(k6221, ":OUTP?")) != 1:
        raise RuntimeError("6221 output did not turn on.")
    actual_peak_current_a = query_float(
        k6221, ":SOUR:WAVE:AMPL?"
    )
    actual_current_rms_a = actual_peak_current_a / math.sqrt(2.0)
    if not math.isclose(
        actual_current_rms_a,
        current_rms_a,
        rel_tol=1e-6,
        abs_tol=1e-12,
    ):
        raise RuntimeError(
            "6221 current readback mismatch after waveform start: "
            f"requested {current_rms_a * 1e3:g} mA RMS, "
            f"received {actual_current_rms_a * 1e3:g} mA RMS."
        )
    return actual_current_rms_a


def interruptible_wait(duration_s, live_plot):
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        live_plot.process_events()
        remaining = deadline - time.monotonic()
        time.sleep(min(0.05, max(0.001, remaining)))
    live_plot.process_events()


def apply_voltage_pulse(
    k2400,
    k6221,
    voltage_v,
    pulse_width_s,
    interlock,
    live_plot,
    global_start,
    pulse_index,
    pulse_gate_result,
):
    if abs(voltage_v) > MAX_PULSE_VOLTAGE_V:
        raise ValueError(
            f"Requested pulse {voltage_v} V exceeds "
            f"±{MAX_PULSE_VOLTAGE_V} V."
        )
    # Keep the gate inside the output function as a final, non-bypassable
    # check immediately before any command can enable the 2400 output.
    require_resistance_gate_for_pulse(
        pulse_gate_result,
        pulse_index,
        voltage_v,
    )

    interlock.begin_voltage()
    try:
        interlock.assert_voltage_allowed()
        confirm_6221_output_off(
            k6221, "immediately before 2400 voltage pulse"
        )
        live_plot.add_pulse_point(
            time.monotonic() - global_start,
            0.0,
            "2400 VOLTAGE phase | SR830 reads blocked",
        )
        write_keithley_checked(k2400, "2400", ":SOUR:VOLT 0")
        write_keithley_checked(k2400, "2400", ":OUTP ON")
        write_keithley_checked(
            k2400, "2400", f":SOUR:VOLT {voltage_v:.12g}"
        )
        live_plot.add_pulse_point(
            time.monotonic() - global_start,
            voltage_v,
            "2400 VOLTAGE phase | SR830 reads blocked",
        )
        interruptible_wait(pulse_width_s, live_plot)
        live_plot.add_pulse_point(
            time.monotonic() - global_start,
            voltage_v,
            "2400 pulse complete | returning to 0 V",
        )
    finally:
        try:
            safe_shutdown_2400(k2400, reset=False)
        finally:
            interlock.end_voltage()
        try:
            live_plot.add_pulse_point(
                time.monotonic() - global_start,
                0.0,
                "2400 at 0 V and output OFF | SR830 still paused",
            )
        except MeasurementStopped:
            raise
        except Exception as exc:
            print(f"Plot update warning after 2400 shutdown: {exc}")


def read_6221_status(k6221):
    if int(query_float(k6221, ":OUTP?")) != 1:
        raise RuntimeError("6221 output turned off during read phase.")
    peak_current_a = query_float(k6221, ":SOUR:WAVE:AMPL?")
    return {
        "k6221_i_peak_readback_a": peak_current_a,
        "k6221_i_rms_readback_a": peak_current_a / math.sqrt(2.0),
        "k6221_frequency_hz": query_float(
            k6221, ":SOUR:WAVE:FREQ?"
        ),
    }


def read_sr830(sr830, interlock):
    interlock.assert_sr830_read_allowed()
    response = query_text(sr830, "SNAP? 1,2,3,4,9")
    values = [float(value) for value in response.split(",")]
    if len(values) != 5:
        raise RuntimeError(f"Unexpected SR830 SNAP response: {response}")
    x_v, y_v, r_v, theta_deg, reference_frequency_hz = values
    lia_status = int(query_float(sr830, "LIAS?"))
    status_messages = parse_lia_status(lia_status)
    return {
        "sr830_x_v": x_v,
        "sr830_y_v": y_v,
        "sr830_r_v": r_v,
        "sr830_theta_deg": theta_deg,
        "sr830_reference_frequency_hz": reference_frequency_hz,
        "sr830_lia_status": lia_status,
        "sr830_status_text": ", ".join(status_messages),
    }


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


def increase_sr830_range_one_step(sr830):
    old_index = int(query_float(sr830, "SENS?"))
    if old_index >= SR830_MAX_SENSITIVITY_INDEX:
        return old_index, old_index, False, "already at maximum range"

    requested_index = old_index + 1
    write_sr830_checked(sr830, f"SENS {requested_index}")
    new_index = int(query_float(sr830, "SENS?"))
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


def read_2400_status(k2400):
    confirm_2400_output_off(k2400, "SR830 acquisition")
    return {
        "k2400_output_state": 0,
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
    raw_path = (
        results_directory / f"6221_SR830_2400_raw_{stamp}.csv"
    )
    summary_path = (
        results_directory / f"6221_SR830_2400_summary_{stamp}.csv"
    )
    figure_path = (
        results_directory / f"6221_SR830_2400_{stamp}.png"
    )
    raw_file = raw_path.open("x", newline="", encoding="utf-8")
    summary_file = summary_path.open("x", newline="", encoding="utf-8")
    return (
        raw_path,
        summary_path,
        figure_path,
        raw_file,
        summary_file,
    )


def select_formal_measurement_rows(rows):
    """Exclude every non-formal row from SR830 point statistics."""
    formal_rows = [
        row
        for row in rows
        if row.get("measurement_stage") == FORMAL_MEASUREMENT_STAGE
    ]
    if not formal_rows:
        raise RuntimeError(
            "No formal SR830 samples are available for averaging."
        )
    excluded_count = len(rows) - len(formal_rows)
    if excluded_count:
        print(
            f"Excluded {excluded_count} pre-measurement SR830 samples "
            "from point statistics."
        )
    return formal_rows


def acquire_sr830_for_duration(
    sr830,
    k6221,
    k2400,
    interlock,
    live_plot,
    writer,
    csv_file,
    global_start,
    pulse_index,
    pulse_voltage_v,
    sr830_harmonic,
    pulse_gate_resistance_ohm,
):
    rows = []
    confirm_2400_output_off(k2400, "before first SR830 query")
    current_sensitivity_index = int(query_float(sr830, "SENS?"))
    read_start = time.monotonic()
    while time.monotonic() - read_start < READ_TIME_PER_PULSE_S:
        loop_start = time.monotonic()
        live_plot.ensure_open()
        interlock.assert_sr830_read_allowed()

        row = {
            "timestamp": datetime.now().isoformat(
                timespec="milliseconds"
            ),
            "measurement_phase": MeasurementInterlock.SR830_READ,
            "measurement_stage": FORMAL_MEASUREMENT_STAGE,
            "global_time_s": time.monotonic() - global_start,
            "pulse_index": pulse_index,
            "pulse_voltage_v": pulse_voltage_v,
            "read_time_s": time.monotonic() - read_start,
            "sr830_harmonic": sr830_harmonic,
            "pulse_gate_resistance_ohm": pulse_gate_resistance_ohm,
        }
        row.update(read_2400_status(k2400))
        row.update(read_6221_status(k6221))
        sr830_values = read_sr830(sr830, interlock)
        row.update(sr830_values)

        overload = bool(sr830_values["sr830_lia_status"] & 0x07)
        row["sr830_overload"] = overload
        row["sr830_sensitivity_index_used"] = (
            current_sensitivity_index
        )
        row["sr830_auto_range_action"] = ""
        range_changed = False
        if overload:
            (
                _old_index,
                current_sensitivity_index,
                range_changed,
                action,
            ) = increase_sr830_range_one_step(sr830)
            row["sr830_auto_range_action"] = action
            print(f"  SR830 overload: {action}")
        row["sr830_sensitivity_index_after_sample"] = (
            current_sensitivity_index
        )

        rows.append(row)
        writer.writerow(
            [
                row["timestamp"],
                row["measurement_stage"],
                pulse_index,
                f"{pulse_voltage_v:.12g}",
                f"{row['k6221_i_rms_readback_a'] * 1e3:.12g}",
                f"{row['k6221_frequency_hz']:.12g}",
                sr830_harmonic,
                f"{1.0 / SAMPLE_INTERVAL_S:.12g}",
                f"{READ_TIME_PER_PULSE_S:.12g}",
                f"{row['read_time_s']:.6f}",
                f"{row['sr830_x_v']:.12e}",
                f"{row['sr830_y_v']:.12e}",
                f"{row['sr830_r_v']:.12e}",
                f"{row['sr830_theta_deg']:.9g}",
                f"{row['sr830_reference_frequency_hz']:.12g}",
                row["sr830_lia_status"],
                row["sr830_status_text"],
                row["sr830_sensitivity_index_used"],
                row["sr830_sensitivity_index_after_sample"],
                row["sr830_auto_range_action"],
                row["k2400_output_state"],
                f"{row['pulse_gate_resistance_ohm']:.12e}",
            ]
        )
        csv_file.flush()
        live_plot.add_read_point(row)

        print(
            f"READ pulse={pulse_index} V={pulse_voltage_v:+.3f} V | "
            f"R={row['sr830_r_v']:+.6e} V | "
            f"theta={row['sr830_theta_deg']:+.3f} deg | "
            f"overload={overload} | "
            f"2400 output confirmed OFF"
        )

        remaining = SAMPLE_INTERVAL_S - (
            time.monotonic() - loop_start
        )
        if remaining > 0:
            interruptible_wait(remaining, live_plot)
    live_plot.set_status(
        f"SR830 READ complete | pulse {pulse_index} | "
        f"{len(rows)} samples | "
        f"{READ_TIME_PER_PULSE_S:.1f}/{READ_TIME_PER_PULSE_S:.1f} s"
    )
    actual_measurement_time_s = time.monotonic() - read_start
    return rows, actual_measurement_time_s


def main():
    readout_settings = ask_readout_settings()
    if readout_settings is None:
        print("Measurement cancelled before instrument connection.")
        return
    output_parent_directory = ask_output_parent_directory()
    if output_parent_directory is None:
        print("Formal recording cancelled before instrument connection.")
        return
    (
        ac_frequency_hz,
        sr830_harmonic,
        ac_current_rms_a,
        start_voltage_v,
        stop_voltage_v,
        voltage_step_v,
    ) = readout_settings
    validate_settings(
        ac_frequency_hz,
        sr830_harmonic,
        ac_current_rms_a,
        start_voltage_v,
        stop_voltage_v,
        voltage_step_v,
    )
    voltage_scan = build_voltage_scan(
        start_voltage_v,
        stop_voltage_v,
        voltage_step_v,
    )
    resource_manager = None
    k2400 = None
    k6221 = None
    sr830 = None
    raw_path = None
    summary_path = None
    figure_path = None
    raw_file = None
    summary_file = None
    live_plot = None
    completed = False
    shutdown_done = False
    interlock = MeasurementInterlock()

    def emergency_output_shutdown():
        """Immediately remove source outputs when the plot is closed."""
        errors = []
        if k2400 is not None:
            try:
                safe_shutdown_2400(k2400, reset=False)
            except Exception as exc:
                errors.append(str(exc))
        if k6221 is not None:
            try:
                stop_6221(k6221, reset=False)
            except Exception as exc:
                errors.append(str(exc))
        interlock.force_idle()

        if errors:
            print("Plot-close shutdown warnings:", "; ".join(errors))
        else:
            print(
                "Plot closed: 2400 set to 0 V/OFF; "
                "6221 waveform stopped/OFF; SR830 reads stopped."
            )

    def shutdown_all():
        nonlocal shutdown_done
        if shutdown_done:
            return
        shutdown_done = True

        errors = []
        if k2400 is not None:
            try:
                safe_shutdown_2400(k2400, reset=True)
            except Exception as exc:
                errors.append(str(exc))
        if k6221 is not None:
            try:
                stop_6221(k6221, reset=True)
            except Exception as exc:
                errors.append(str(exc))
        interlock.force_idle()

        for instrument in (sr830, k6221, k2400):
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

        if errors:
            print("Shutdown warnings:", "; ".join(errors))
        else:
            print(
                "2400 set to 0 V/OFF; 6221 stopped/OFF; "
                "SR830 queries stopped."
            )

    try:
        (
            resource_manager,
            k2400,
            k6221,
            sr830,
        ) = connect_instruments()
        setup_2400(k2400)
        (
            actual_frequency_hz,
            actual_current_rms_a,
        ) = setup_6221(
            k6221,
            ac_frequency_hz,
            ac_current_rms_a,
        )
        actual_harmonic = setup_sr830(sr830, sr830_harmonic)
        confirm_2400_output_off(k2400, "after setup")
        confirm_6221_output_off(k6221, "after setup")

        (
            raw_path,
            summary_path,
            figure_path,
            raw_file,
            summary_file,
        ) = create_output_files(output_parent_directory)
        raw_writer = csv.writer(raw_file)
        raw_writer.writerow(RAW_CSV_HEADER)
        summary_writer = csv.writer(summary_file)
        summary_writer.writerow(SUMMARY_CSV_HEADER)
        print(f"Saving raw data to: {raw_path}")
        print(f"Saving summary to:  {summary_path}")
        print(f"Saving plot to:     {figure_path}")

        live_plot = LivePlot(
            actual_harmonic,
            actual_frequency_hz,
            actual_current_rms_a,
            close_callback=emergency_output_shutdown,
        )
        global_start = time.monotonic()

        # The first pulse is gated by an initial resistance measurement.
        # Each later pulse uses the resistance measured after the preceding
        # completed SR830 point.
        print(
            "Measuring initial resistance before the first voltage pulse..."
        )
        pulse_gate_result = measure_2400_resistance(
            k2400,
            k6221,
            interlock,
            live_plot,
        )
        print(
            "Initial 2400 resistance = "
            f"{pulse_gate_result['k2400_resistance_ohm']:+.6e} Ohm"
        )

        for pulse_index, voltage_v in enumerate(voltage_scan, start=1):
            live_plot.ensure_open()

            # Explicitly stop read current before entering voltage phase.
            stop_6221(k6221, reset=False)
            confirm_6221_output_off(
                k6221, "before entering 2400 voltage phase"
            )

            pulse_gate_resistance_ohm = (
                require_resistance_gate_for_pulse(
                    pulse_gate_result,
                    pulse_index,
                    voltage_v,
                )
            )
            gate_status = (
                f"Pulse {pulse_index} allowed: resistance "
                f"{pulse_gate_resistance_ohm / 1e6:.6g} MOhm > "
                f"{MIN_RESISTANCE_FOR_PULSE_OHM / 1e6:g} MOhm"
            )
            print(gate_status)
            live_plot.set_status(gate_status)

            print(
                f"\nPulse {pulse_index}/{len(voltage_scan)}: "
                f"{voltage_v:+.3f} V"
            )
            apply_voltage_pulse(
                k2400,
                k6221,
                voltage_v,
                PULSE_WIDTH_S,
                interlock,
                live_plot,
                global_start,
                pulse_index,
                pulse_gate_result,
            )
            confirm_2400_output_off(
                k2400, "immediately after voltage pulse"
            )

            # IDLE phase: no 2400 voltage and no SR830 query.
            if interlock.phase != MeasurementInterlock.IDLE:
                raise RuntimeError("Interlock failed to return to IDLE.")
            print(
                f"Settling {POST_PULSE_SETTLE_S:.3f} s with "
                "SR830 queries paused."
            )
            interruptible_wait(POST_PULSE_SETTLE_S, live_plot)

            # Start the AC probe and let the lock-in settle while SR830
            # measurement queries remain blocked by the IDLE interlock.
            confirm_2400_output_off(
                k2400, "before starting 6221 read current"
            )
            rows = []
            actual_measurement_time_s = 0.0
            read_phase_started = False
            try:
                actual_current_rms_a = start_6221(
                    k6221, actual_current_rms_a
                )
                live_plot.add_current_point(
                    time.monotonic() - global_start,
                    actual_current_rms_a * 1e3,
                    f"6221 {actual_current_rms_a * 1e3:.9g} mA RMS "
                    f"started | pre-running "
                    f"{SR830_SETTLE_AFTER_6221_S:g} s | "
                    "SR830 queries blocked",
                )
                interruptible_wait(
                    SR830_SETTLE_AFTER_6221_S,
                    live_plot,
                )
                confirm_2400_output_off(
                    k2400, "immediately before SR830 read phase"
                )
                interlock.begin_sr830_read()
                read_phase_started = True
                (
                    rows,
                    actual_measurement_time_s,
                ) = acquire_sr830_for_duration(
                    sr830,
                    k6221,
                    k2400,
                    interlock,
                    live_plot,
                    raw_writer,
                    raw_file,
                    global_start,
                    pulse_index,
                    voltage_v,
                    actual_harmonic,
                    pulse_gate_resistance_ohm,
                )
            finally:
                if read_phase_started:
                    interlock.end_sr830_read()
                stop_6221(k6221, reset=False)
                confirm_6221_output_off(
                    k6221, "after SR830 read phase"
                )

            if not rows:
                raise RuntimeError(
                    f"No SR830 samples for pulse {voltage_v} V."
                )
            measurement_rows = select_formal_measurement_rows(rows)
            mean_r_v = float(
                np.mean([row["sr830_r_v"] for row in measurement_rows])
            )
            std_r_v = float(
                np.std([row["sr830_r_v"] for row in measurement_rows])
            )
            mean_x_v = float(
                np.mean(
                    [row["sr830_x_v"] for row in measurement_rows]
                )
            )
            mean_y_v = float(
                np.mean(
                    [row["sr830_y_v"] for row in measurement_rows]
                )
            )
            mean_theta_deg = float(
                np.mean(
                    [
                        row["sr830_theta_deg"]
                        for row in measurement_rows
                    ]
                )
            )
            mean_reference_frequency_hz = float(
                np.mean(
                    [
                        row["sr830_reference_frequency_hz"]
                        for row in measurement_rows
                    ]
                )
            )
            bad_status_count = sum(
                row["sr830_lia_status"] != 0
                for row in measurement_rows
            )
            auto_range_adjustment_count = sum(
                row["sr830_auto_range_action"].startswith(
                    "auto range SENS"
                )
                for row in measurement_rows
            )
            final_sensitivity_index = measurement_rows[-1][
                "sr830_sensitivity_index_after_sample"
            ]
            if actual_measurement_time_s <= 0:
                raise RuntimeError(
                    "SR830 actual measurement time must be positive."
                )
            achieved_sampling_frequency_hz = (
                len(measurement_rows) / actual_measurement_time_s
            )

            print("Measuring resistance with Keithley 2400...")
            resistance_result = measure_2400_resistance(
                k2400,
                k6221,
                interlock,
                live_plot,
            )
            print(
                "2400 resistance = "
                f"{resistance_result['k2400_resistance_ohm']:+.6e} Ohm "
                f"at source current "
                f"{resistance_result['k2400_resistance_source_current_a']:.3e} A"
            )

            summary_writer.writerow(
                [
                    pulse_index,
                    f"{voltage_v:.12g}",
                    f"{measurement_rows[-1]['k6221_i_rms_readback_a'] * 1e3:.12g}",
                    f"{measurement_rows[-1]['k6221_frequency_hz']:.12g}",
                    actual_harmonic,
                    f"{1.0 / SAMPLE_INTERVAL_S:.12g}",
                    f"{READ_TIME_PER_PULSE_S:.12g}",
                    f"{actual_measurement_time_s:.9g}",
                    f"{achieved_sampling_frequency_hz:.9g}",
                    "R",
                    len(measurement_rows),
                    f"{mean_r_v:.12e}",
                    f"{std_r_v:.12e}",
                    f"{mean_x_v:.12e}",
                    f"{mean_y_v:.12e}",
                    f"{mean_r_v:.12e}",
                    f"{mean_theta_deg:.9g}",
                    f"{mean_reference_frequency_hz:.12g}",
                    bad_status_count,
                    auto_range_adjustment_count,
                    final_sensitivity_index,
                    f"{pulse_gate_resistance_ohm:.12e}",
                    f"{resistance_result['k2400_resistance_source_current_a']:.12e}",
                    f"{resistance_result['k2400_resistance_voltage_v']:.12e}",
                    f"{resistance_result['k2400_resistance_current_a']:.12e}",
                    f"{resistance_result['k2400_resistance_ohm']:.12e}",
                ]
            )
            summary_file.flush()
            live_plot.add_summary_point(
                voltage_v,
                mean_r_v,
                resistance_result["k2400_resistance_ohm"],
            )
            print(
                f"Mean SR830 R = {mean_r_v:+.6e} V | "
                f"n={len(measurement_rows)}, "
                f"status flags={bad_status_count} | "
                f"2400 resistance="
                f"{resistance_result['k2400_resistance_ohm']:+.6e} Ohm"
            )
            live_plot.figure.savefig(figure_path, dpi=180)

            # This post-point measurement is the safety gate for the next
            # voltage pulse. The final point is still followed by a resistance
            # read, but no additional pulse is generated.
            pulse_gate_result = resistance_result

        completed = True
        print("\nMeasurement completed.")
        live_plot.set_status(
            "Measurement completed; instruments safely stopped."
        )
        live_plot.figure.savefig(figure_path, dpi=180)

    except MeasurementStopped:
        print("\nPlot closed. Measurement stopped.")
    except ResistanceGateBlocked as exc:
        print(f"\nResistance safety gate stopped the measurement: {exc}")
        if live_plot is not None:
            try:
                live_plot.set_status(
                    "STOPPED: resistance must be above "
                    f"{MIN_RESISTANCE_FOR_PULSE_OHM / 1e6:g} MOhm; "
                    "no voltage pulse was applied."
                )
            except MeasurementStopped:
                pass
    except KeyboardInterrupt:
        print("\nCtrl+C received. Measurement stopped.")
    finally:
        if live_plot is not None:
            live_plot.close_callback = None
        shutdown_all()
        if raw_file is not None:
            raw_file.close()
        if summary_file is not None:
            summary_file.close()

    if raw_path is not None:
        print(f"Raw data: {raw_path}")
    if summary_path is not None:
        print(f"Summary:  {summary_path}")
    if figure_path is not None and figure_path.exists():
        print(f"Plot:     {figure_path}")

    if (
        completed
        and live_plot is not None
        and plt.fignum_exists(live_plot.figure.number)
    ):
        plt.ioff()
        plt.show()


if __name__ == "__main__":
    main()
