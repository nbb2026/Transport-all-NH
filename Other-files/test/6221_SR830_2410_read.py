import time
import csv
from datetime import datetime
from pathlib import Path
import numpy as np
import pyvisa
import matplotlib.pyplot as plt


# =========================
# Instrument addresses
# =========================

ADDR_K2410 = "GPIB0::24::INSTR"
ADDR_K6221 = "GPIB0::30::INSTR"
ADDR_SR830 = "GPIB0::8::INSTR"


# =========================
# Measurement timing parameters
# =========================

pulse_width = 1.0
settle_time = 0.5
read_time = 10.0
sample_interval = 0.5


# =========================
# AC current excitation parameters
# =========================

ac_current_rms = 1e-3
ac_current_peak = np.sqrt(2) * ac_current_rms
ac_frequency = 13.7
TRIGGER_LINK_LINE = 4

RESULTS_DIRECTORY = (
    Path(__file__).resolve().parent / f"{Path(__file__).stem}_Results"
)
output_file = RESULTS_DIRECTORY / "nonlocal_voltage_no_switchbox.csv"


def select_voltage_display_unit(values_v):
    max_abs_v = max((abs(value) for value in values_v), default=0.0)
    if max_abs_v >= 1e-1:
        return 1.0, "V"
    if max_abs_v >= 1e-4:
        return 1e3, "mV"
    if max_abs_v >= 1e-7:
        return 1e6, "uV"
    return 1e9, "nV"


# =========================
# Voltage scan parameters
# =========================

Vmax = 20.0
voltage_step = 4.0

def build_scan_segment(start, stop, step):
    if step <= 0:
        raise ValueError("voltage_step must be > 0")
    values = []
    direction = 1.0 if stop >= start else -1.0
    value = float(start)
    while (value < stop) if direction > 0 else (value > stop):
        values.append(round(value, 12))
        value += direction * step
    if not values or not np.isclose(values[-1], stop, rtol=0.0, atol=1e-12):
        values.append(float(stop))
    return values


voltage_scan_down = build_scan_segment(Vmax, -Vmax, voltage_step)
voltage_scan_up = build_scan_segment(-Vmax, Vmax, voltage_step)
voltage_scan = np.asarray(voltage_scan_down + voltage_scan_up[1:], dtype=float)


class MeasurementStopped(Exception):
    """Internal signal used when the live plot is closed."""


class LivePlot:
    def __init__(self, shutdown_callback=None):
        plt.ion()

        self.fig, self.ax = plt.subplots(3, 2, figsize=(13, 9))
        self.fig.suptitle("Real-time measurement")
        self.stop_requested = False
        self.shutdown_callback = shutdown_callback
        self.fig.canvas.mpl_connect("close_event", self._on_close)

        self.ax_pulse = self.ax[0, 0]
        self.ax_i6221 = self.ax[0, 1]
        self.ax_sr830_r_time = self.ax[1, 0]
        self.ax_theta = self.ax[1, 1]
        self.ax_freq = self.ax[2, 0]
        self.ax_sr830_r_vs_pulse = self.ax[2, 1]

        self.line_pulse, = self.ax_pulse.plot([], [], "-", lw=1.5)
        self.line_i6221, = self.ax_i6221.plot([], [], "o-", ms=3)
        self.line_sr830_r_time, = self.ax_sr830_r_time.plot([], [], "o-", ms=3)
        self.line_theta, = self.ax_theta.plot([], [], "o-", ms=3)
        self.line_f6221, = self.ax_freq.plot([], [], "o-", ms=3, label="6221 freq readback")
        self.line_fsr830, = self.ax_freq.plot([], [], "o-", ms=3, label="SR830 ref freq measured")
        self.line_sr830_r_vs_pulse, = self.ax_sr830_r_vs_pulse.plot([], [], "o-", ms=4)

        self.ax_pulse.set_xlabel("Global time (s)")
        self.ax_pulse.set_ylabel("2410 command voltage (V)")
        self.ax_pulse.set_title("2410 command voltage vs time")

        self.ax_i6221.set_xlabel("Global time (s)")
        self.ax_i6221.set_ylabel("6221 I rms readback (A)")
        self.ax_i6221.set_title("6221 I rms readback vs time")

        self.ax_sr830_r_time.set_xlabel("Global time (s)")
        self.ax_sr830_r_time.set_ylabel("SR830 R measured (V)")
        self.ax_sr830_r_time.set_title("SR830 R measured vs time")

        self.ax_theta.set_xlabel("Global time (s)")
        self.ax_theta.set_ylabel("SR830 theta measured (deg)")
        self.ax_theta.set_title("SR830 theta measured vs time")

        self.ax_freq.set_xlabel("Global time (s)")
        self.ax_freq.set_ylabel("Frequency (Hz)")
        self.ax_freq.set_title("Frequency vs time")
        self.ax_freq.legend(loc="best")

        self.ax_sr830_r_vs_pulse.set_xlabel("2410 pulse voltage (V)")
        self.ax_sr830_r_vs_pulse.set_ylabel("Mean SR830 R measured (V)")
        self.ax_sr830_r_vs_pulse.set_title("SR830 R measured vs pulse voltage")

        for axis in self.ax.flat:
            axis.grid(True)

        self.t_pulse = []
        self.v_pulse = []

        self.t_read = []
        self.sr830_r = []
        self.i6221_rms = []
        self.f6221 = []
        self.fsr830 = []
        self.theta = []

        self.pulse_voltage_points = []
        self.mean_sr830_r_points = []

        plt.tight_layout()
        plt.pause(0.1)

    def _on_close(self, _event):
        self.stop_requested = True
        if self.shutdown_callback is not None:
            self.shutdown_callback()

    def ensure_open(self):
        if self.stop_requested or not plt.fignum_exists(self.fig.number):
            self.stop_requested = True
            raise MeasurementStopped("Live plot closed by user.")

    def _finite_array(self, values):
        arr = np.asarray(values, dtype=float)
        return arr[np.isfinite(arr)]

    def _autoscale_y(self, axis):
        y_all = []

        for line in axis.lines:
            y = self._finite_array(line.get_ydata())
            if len(y) > 0:
                y_all.append(y)

        if not y_all:
            return

        y_all = np.concatenate(y_all)
        ymin = np.min(y_all)
        ymax = np.max(y_all)

        if ymin == ymax:
            margin = abs(ymin) * 0.05 if ymin != 0 else 1.0
        else:
            margin = 0.08 * (ymax - ymin)

        axis.set_ylim(ymin - margin, ymax + margin)

    def _sync_time_axes(self):
        all_t = []

        for values in [self.t_pulse, self.t_read]:
            t = self._finite_array(values)
            if len(t) > 0:
                all_t.append(t)

        if not all_t:
            return

        all_t = np.concatenate(all_t)
        xmin = np.min(all_t)
        xmax = np.max(all_t)

        if xmin == xmax:
            xmax = xmin + 1.0

        margin = 0.02 * (xmax - xmin)

        for axis in [
            self.ax_pulse,
            self.ax_i6221,
            self.ax_sr830_r_time,
            self.ax_theta,
            self.ax_freq
        ]:
            axis.set_xlim(xmin - margin, xmax + margin)

    def add_pulse_point(self, global_time, voltage):
        self.t_pulse.append(global_time)
        self.v_pulse.append(voltage)

        self.line_pulse.set_data(self.t_pulse, self.v_pulse)

        self._sync_time_axes()
        self._autoscale_y(self.ax_pulse)
        self._draw()

    def add_read_point(self, row):
        t = row["global_time_s"]

        self.t_read.append(t)
        self.sr830_r.append(row["sr830_r_measured_v"])
        self.i6221_rms.append(row["k6221_i_rms_readback_a"])
        self.f6221.append(row["k6221_freq_readback_hz"])
        self.fsr830.append(row["sr830_ref_freq_measured_hz"])
        self.theta.append(row["sr830_theta_measured_deg"])

        voltage_scale, voltage_unit = select_voltage_display_unit(
            self.sr830_r
        )
        self.line_i6221.set_data(self.t_read, self.i6221_rms)
        self.line_sr830_r_time.set_data(
            self.t_read,
            [value * voltage_scale for value in self.sr830_r],
        )
        self.ax_sr830_r_time.set_ylabel(
            f"SR830 R measured ({voltage_unit})"
        )
        self.line_theta.set_data(self.t_read, self.theta)
        self.line_f6221.set_data(self.t_read, self.f6221)
        self.line_fsr830.set_data(self.t_read, self.fsr830)

        self._sync_time_axes()
        self._autoscale_y(self.ax_i6221)
        self._autoscale_y(self.ax_sr830_r_time)
        self._autoscale_y(self.ax_theta)
        self._autoscale_y(self.ax_freq)
        self._draw()

    def add_pulse_summary(self, pulse_voltage, mean_sr830_r):
        self.pulse_voltage_points.append(pulse_voltage)
        self.mean_sr830_r_points.append(mean_sr830_r)
        voltage_scale, voltage_unit = select_voltage_display_unit(
            self.mean_sr830_r_points
        )

        self.line_sr830_r_vs_pulse.set_data(
            self.pulse_voltage_points,
            [
                value * voltage_scale
                for value in self.mean_sr830_r_points
            ],
        )
        self.ax_sr830_r_vs_pulse.set_ylabel(
            f"Mean SR830 R measured ({voltage_unit})"
        )

        self.ax_sr830_r_vs_pulse.relim()
        self.ax_sr830_r_vs_pulse.autoscale_view()
        self._autoscale_y(self.ax_sr830_r_vs_pulse)
        self._draw()

    def _draw(self):
        self.ensure_open()
        self.fig.canvas.draw()
        self.fig.canvas.flush_events()
        plt.pause(0.01)
        self.ensure_open()


def connect_instruments():
    rm = pyvisa.ResourceManager()
    opened = []
    try:
        for address in (ADDR_K2410, ADDR_K6221, ADDR_SR830):
            inst = rm.open_resource(address)
            opened.append(inst)
            inst.timeout = 10000
            inst.write_termination = "\n"
            inst.read_termination = "\n"

        k2410, k6221, sr830 = opened
        # The SR830 manual requires OUTX 1 at the start of a GPIB program.
        sr830.write("OUTX 1")
        verify_identity(k2410, "2410", ADDR_K2410)
        verify_identity(k6221, "6221", ADDR_K6221)
        verify_identity(sr830, "SR830", ADDR_SR830)
        return rm, k2410, k6221, sr830
    except Exception:
        for inst in opened:
            try:
                inst.close()
            except Exception:
                pass
        rm.close()
        raise


def verify_identity(inst, expected_model, address):
    identity = inst.query("*IDN?").strip()
    if expected_model not in identity.upper():
        raise RuntimeError(f"Unexpected instrument at {address}: {identity}")
    return identity


def assert_no_scpi_error(inst, model, context):
    error = inst.query("SYST:ERR?").strip()
    error_code = int(error.split(",", 1)[0].strip())
    if error_code != 0:
        raise RuntimeError(f"{model} SCPI error after {context}: {error}")


def write_scpi_checked(inst, model, command):
    inst.write(command)
    assert_no_scpi_error(inst, model, repr(command))


def write_sr830_checked(sr830, command):
    sr830.write(command)
    event_status = int(sr830.query("*ESR?").strip())
    if event_status & 0x30:
        raise RuntimeError(
            f"SR830 rejected {command!r}; *ESR? returned {event_status}"
        )


def timestamped_output_path(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return path.with_name(f"{path.stem}_{stamp}{path.suffix}")


def setup_k2410(k2410):
    k2410.write("*RST")
    k2410.write("*CLS")
    for command in (
        ":SOUR:FUNC VOLT",
        ":SOUR:VOLT:MODE FIXED",
        ":SENS:FUNC 'CURR'",
        ":SENS:CURR:PROT 1e-3",
        ":FORM:ELEM VOLT,CURR",
        ":SOUR:VOLT 0",
        ":OUTP ON",
    ):
        write_scpi_checked(k2410, "2410", command)


def setup_k6221(k6221):
    k6221.write("*RST")
    k6221.write("*CLS")
    for command in (
        ":CURR:COMP 50",
        ":SOUR:WAVE:FUNC SIN",
        f":SOUR:WAVE:FREQ {ac_frequency}",
        f":SOUR:WAVE:AMPL {ac_current_peak}",
        ":SOUR:WAVE:OFFS 0",
        ":SOUR:WAVE:RANG BEST",
        ":SOUR:WAVE:PMAR 0",
        f":SOUR:WAVE:PMAR:OLIN {TRIGGER_LINK_LINE}",
        ":SOUR:WAVE:PMAR:STAT ON",
        ":SOUR:WAVE:ARM",
    ):
        write_scpi_checked(k6221, "6221", command)
    marker_enabled = int(query_float(k6221, ":SOUR:WAVE:PMAR:STAT?"))
    marker_line = int(query_float(k6221, ":SOUR:WAVE:PMAR:OLIN?"))
    if marker_enabled != 1 or marker_line != TRIGGER_LINK_LINE:
        raise RuntimeError(
            f"6221 phase marker mismatch: enabled={marker_enabled}, "
            f"line={marker_line}"
        )


def setup_sr830(sr830):
    sr830.write("*RST")
    sr830.write("OUTX 1")
    sr830.write("*CLS")
    for command in (
        "FMOD 0",
        "RSLP 2",  # 6221 Trigger Link phase marker: TTL falling edge
        "ISRC 0",
        "IGND 0",
        "ICPL 0",
        "OFLT 10",
        "SENS 20",
    ):
        write_sr830_checked(sr830, command)

    time.sleep(0.2)

    reference_mode = int(query_float(sr830, "FMOD?"))
    reference_slope = int(query_float(sr830, "RSLP?"))
    if reference_mode != 0 or reference_slope != 2:
        raise RuntimeError(
            f"SR830 reference mismatch: FMOD={reference_mode}, "
            f"RSLP={reference_slope}"
        )
    ref_freq = float(sr830.query("FREQ?").strip())
    print(f"SR830 reference frequency = {ref_freq:.6f} Hz")


def read_k6221_status(k6221):
    """
    6221 SOUR:WAVE:AMPL? returns waveform peak amplitude readback.
    It is not an independent measured current.
    """
    output_on = int(float(k6221.query(":OUTP?").strip())) == 1
    if not output_on:
        raise RuntimeError("6221 output unexpectedly turned OFF during acquisition")
    i_peak_readback = float(k6221.query(":SOUR:WAVE:AMPL?").strip())
    freq_readback = float(k6221.query(":SOUR:WAVE:FREQ?").strip())
    i_rms_readback = i_peak_readback / np.sqrt(2)

    return {
        "k6221_i_peak_readback_a": i_peak_readback,
        "k6221_i_rms_readback_a": i_rms_readback,
        "k6221_freq_readback_hz": freq_readback
    }


def read_sr830_status(sr830):
    """
    SR830 measured values.

    SNAP? 1,2,3,4,9 returns:
        1 = X
        2 = Y
        3 = R
        4 = theta
        9 = reference frequency
    """
    try:
        raw = sr830.query("SNAP? 1,2,3,4,9").strip()
        vals = [float(x) for x in raw.split(",")]

        x = vals[0]
        y = vals[1]
        r = vals[2]
        theta = vals[3]
        ref_freq = vals[4]

    except Exception:
        x = float(sr830.query("OUTP? 1").strip())
        y = float(sr830.query("OUTP? 2").strip())
        r = float(sr830.query("OUTP? 3").strip())
        theta = float(sr830.query("OUTP? 4").strip())
        ref_freq = float(sr830.query("FREQ?").strip())

    return {
        "sr830_x_measured_v": x,
        "sr830_y_measured_v": y,
        "sr830_r_measured_v": r,
        "sr830_theta_measured_deg": theta,
        "sr830_ref_freq_measured_hz": ref_freq
    }


def read_k2410_status(k2410):
    """
    For Source V + Measure I configuration, :READ? usually returns:
        voltage element = voltage source/source readback value
        current element = measured current
    """
    raw = k2410.query(":READ?").strip()
    vals = [float(x) for x in raw.split(",")]
    if len(vals) < 2:
        raise RuntimeError(f"Unexpected 2410 :READ? response: {raw!r}")
    v_source_readback = vals[0]
    i_measured = vals[1]

    return {
        "k2410_v_source_readback_v": v_source_readback,
        "k2410_i_measured_a": i_measured
    }


def apply_voltage_pulse(k2410, voltage, width, live_plot, global_t0):
    t0 = time.time() - global_t0
    live_plot.add_pulse_point(t0, 0.0)

    try:
        write_scpi_checked(k2410, "2410", f":SOUR:VOLT {voltage}")
        t1 = time.time() - global_t0
        live_plot.add_pulse_point(t1, voltage)
        time.sleep(width)
        t2 = time.time() - global_t0
        live_plot.add_pulse_point(t2, voltage)
    finally:
        k2410.write(":SOUR:VOLT 0")
        t3 = time.time() - global_t0
        live_plot.add_pulse_point(t3, 0.0)


def start_ac_current(k6221):
    write_scpi_checked(k6221, "6221", ":SOUR:WAVE:INIT")


def stop_ac_current(k6221):
    errors = []
    for command in (":SOUR:WAVE:ABOR", ":OUTP OFF"):
        try:
            k6221.write(command)
        except Exception as exc:
            errors.append(f"{command}: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))


def shutdown_ac_source(k6221):
    errors = []
    for command in (
        ":SOUR:WAVE:ABOR",
        ":CURR 0",
        ":OUTP OFF",
        "*RST",
        "*CLS",
    ):
        try:
            k6221.write(command)
        except Exception as exc:
            errors.append(f"{command}: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))


def save_header(filename):
    with open(filename, "x", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "global_time_s",
            "pulse_index",
            "pulse_voltage_v",
            "read_time_s",

            "k6221_i_peak_readback_a",
            "k6221_i_rms_readback_a",
            "k6221_freq_readback_hz",

            "sr830_x_measured_v",
            "sr830_y_measured_v",
            "sr830_r_measured_v",
            "sr830_theta_measured_deg",
            "sr830_ref_freq_measured_hz",

            "k2410_v_source_readback_v",
            "k2410_i_measured_a"
        ])


def append_row(filename, row):
    with open(filename, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            row["global_time_s"],
            row["pulse_index"],
            row["pulse_voltage_v"],
            row["read_time_s"],

            row["k6221_i_peak_readback_a"],
            row["k6221_i_rms_readback_a"],
            row["k6221_freq_readback_hz"],

            row["sr830_x_measured_v"],
            row["sr830_y_measured_v"],
            row["sr830_r_measured_v"],
            row["sr830_theta_measured_deg"],
            row["sr830_ref_freq_measured_hz"],

            row["k2410_v_source_readback_v"],
            row["k2410_i_measured_a"]
        ])


def read_instruments_for_duration(
    sr830,
    k6221,
    k2410,
    duration,
    interval,
    live_plot,
    global_t0,
    pulse_index,
    pulse_voltage,
    output_file
):
    data = []
    read_t0 = time.time()

    while time.time() - read_t0 < duration:
        live_plot.ensure_open()
        now = time.time()
        global_time = now - global_t0
        read_time = now - read_t0

        k6221_status = read_k6221_status(k6221)
        sr830_status = read_sr830_status(sr830)
        k2410_status = read_k2410_status(k2410)

        row = {
            "global_time_s": global_time,
            "pulse_index": pulse_index,
            "pulse_voltage_v": pulse_voltage,
            "read_time_s": read_time,

            "k6221_i_peak_readback_a": k6221_status["k6221_i_peak_readback_a"],
            "k6221_i_rms_readback_a": k6221_status["k6221_i_rms_readback_a"],
            "k6221_freq_readback_hz": k6221_status["k6221_freq_readback_hz"],

            "sr830_x_measured_v": sr830_status["sr830_x_measured_v"],
            "sr830_y_measured_v": sr830_status["sr830_y_measured_v"],
            "sr830_r_measured_v": sr830_status["sr830_r_measured_v"],
            "sr830_theta_measured_deg": sr830_status["sr830_theta_measured_deg"],
            "sr830_ref_freq_measured_hz": sr830_status["sr830_ref_freq_measured_hz"],

            "k2410_v_source_readback_v": k2410_status["k2410_v_source_readback_v"],
            "k2410_i_measured_a": k2410_status["k2410_i_measured_a"]
        }

        print(
            f"t = {global_time:8.2f} s | "
            f"Vpulse = {pulse_voltage: .3f} V | "
            f"6221 I_rms readback = {row['k6221_i_rms_readback_a']:.6e} A, "
            f"6221 f readback = {row['k6221_freq_readback_hz']:.3f} Hz | "
            f"SR830 f = {row['sr830_ref_freq_measured_hz']:.3f} Hz, "
            f"X = {row['sr830_x_measured_v']:.6e} V, "
            f"Y = {row['sr830_y_measured_v']:.6e} V, "
            f"R = {row['sr830_r_measured_v']:.6e} V, "
            f"theta = {row['sr830_theta_measured_deg']:.3f} deg | "
            f"2410 V source/readback = {row['k2410_v_source_readback_v']:.6e} V, "
            f"2410 I measured = {row['k2410_i_measured_a']:.6e} A"
        )

        data.append(row)
        append_row(output_file, row)
        live_plot.add_read_point(row)

        time.sleep(interval)

    return data


def main():
    rm, k2410, k6221, sr830 = connect_instruments()
    run_output_file = timestamped_output_path(output_file)
    live_plot = None
    completed = False

    try:
        def emergency_plot_shutdown():
            errors = []
            for command in (":SOUR:VOLT 0", ":OUTP OFF"):
                try:
                    k2410.write(command)
                except Exception as exc:
                    errors.append(f"2410 {command}: {exc}")
            try:
                shutdown_ac_source(k6221)
            except Exception as exc:
                errors.append(f"6221 shutdown: {exc}")
            if errors:
                print("Plot-close shutdown warning:", "; ".join(errors))
            else:
                print("Plot closed. Source outputs safely stopped.")

        live_plot = LivePlot(shutdown_callback=emergency_plot_shutdown)
        save_header(run_output_file)
        print(f"Saving data to {run_output_file.resolve()}")
        setup_k2410(k2410)
        setup_k6221(k6221)
        setup_sr830(sr830)

        global_t0 = time.time()
        live_plot.add_pulse_point(0.0, 0.0)

        for i, voltage in enumerate(voltage_scan):
            print(f"Pulse {i + 1}/{len(voltage_scan)}: Vpulse = {voltage:.3f} V")

            apply_voltage_pulse(
                k2410=k2410,
                voltage=voltage,
                width=pulse_width,
                live_plot=live_plot,
                global_t0=global_t0
            )

            time.sleep(settle_time)
            live_plot.ensure_open()

            start_ac_current(k6221)

            read_data = read_instruments_for_duration(
                sr830=sr830,
                k6221=k6221,
                k2410=k2410,
                duration=read_time,
                interval=sample_interval,
                live_plot=live_plot,
                global_t0=global_t0,
                pulse_index=i,
                pulse_voltage=voltage,
                output_file=run_output_file
            )

            stop_ac_current(k6221)

            sr830_r_values = np.array([d["sr830_r_measured_v"] for d in read_data])
            mean_sr830_r = np.nanmean(sr830_r_values)

            live_plot.add_pulse_summary(voltage, mean_sr830_r)

            print(f"  mean SR830 R measured = {mean_sr830_r:.6e} V")

        completed = True
    except MeasurementStopped:
        print("Live plot closed. Measurement stopped.")
    finally:
        if live_plot is not None:
            live_plot.shutdown_callback = None
        shutdown_errors = []
        for command in (
            ":SOUR:VOLT 0",
            ":OUTP OFF",
            "*RST",
            "*CLS",
        ):
            try:
                k2410.write(command)
            except Exception as exc:
                shutdown_errors.append(f"2410 {command}: {exc}")

        try:
            shutdown_ac_source(k6221)
        except Exception as exc:
            shutdown_errors.append(f"6221 shutdown: {exc}")

        for resource in (k2410, k6221, sr830):
            try:
                resource.close()
            except Exception:
                pass
        try:
            rm.close()
        except Exception:
            pass

        if shutdown_errors:
            print("WARNING: output shutdown could not be confirmed:")
            for error in shutdown_errors:
                print("  ", error)
        else:
            print("Measurement finished. Instrument outputs were shut down.")

    if (
        completed
        and live_plot is not None
        and plt.fignum_exists(live_plot.fig.number)
    ):
        plt.ioff()
        plt.show()


if __name__ == "__main__":
    main()
