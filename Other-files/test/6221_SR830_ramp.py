import csv
import time
from pathlib import Path
from datetime import datetime
import math

import pyvisa
import matplotlib.pyplot as plt


KEITHLEY_6221_ADDR = "GPIB0::30::INSTR"
SR830_ADDR = "GPIB0::8::INSTR"

SOURCE_FREQ_HZ = 13.7

# Current ramp settings in RMS.
# The Keithley 6221 SCPI command SOUR:WAVE:AMPL uses peak amplitude internally.
# This code lets you enter RMS current values here, then converts them to peak
# before sending them to the 6221.
START_CURRENT_RMS_A = 0.1e-3
STOP_CURRENT_RMS_A = 0.5e-3
STEP_CURRENT_RMS_A = 0.1e-3

STEP_MEASURE_TIME_S = 10.0
SAMPLE_INTERVAL_S = 1.0

HARMONIC = 1
TRIGGER_LINK_LINE = 4

SAVE_CSV = True
RESULTS_DIRECTORY = (
    Path(__file__).resolve().parent / f"{Path(__file__).stem}_Results"
)
CSV_PATH = RESULTS_DIRECTORY / "sr830_6221_ramp_log.csv"


def select_voltage_display_unit(values_v):
    max_abs_v = max((abs(value) for value in values_v), default=0.0)
    if max_abs_v >= 1e-1:
        return 1.0, "V"
    if max_abs_v >= 1e-4:
        return 1e3, "mV"
    if max_abs_v >= 1e-7:
        return 1e6, "uV"
    return 1e9, "nV"

def rms_to_peak(current_rms_a):
    return math.sqrt(2.0) * current_rms_a


def peak_to_rms(current_peak_a):
    return current_peak_a / math.sqrt(2.0)


def write(inst, cmd):
    inst.write(cmd)
    time.sleep(0.05)


def query_text(inst, cmd):
    return inst.query(cmd).strip()


def query_float(inst, cmd):
    return float(query_text(inst, cmd))


def verify_identity(inst, expected_model, address):
    identity = query_text(inst, "*IDN?")
    if expected_model not in identity.upper():
        raise RuntimeError(f"Unexpected instrument at {address}: {identity}")
    return identity


def timestamped_csv_path(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return path.with_name(f"{path.stem}_{stamp}{path.suffix}")


def assert_no_6221_error(src, context):
    error = query_text(src, "SYST:ERR?")
    error_code = int(error.split(",", 1)[0].strip())
    if error_code != 0:
        raise RuntimeError(f"6221 SCPI error after {context}: {error}")


def write_6221_checked(src, command):
    write(src, command)
    assert_no_6221_error(src, repr(command))


def write_sr830_checked(lockin, command):
    write(lockin, command)
    event_status = int(query_text(lockin, "*ESR?"))
    if event_status & 0x30:
        raise RuntimeError(
            f"SR830 rejected {command!r}; *ESR? returned {event_status}"
        )


def connect_instruments():
    rm = pyvisa.ResourceManager()
    opened = []
    try:
        for address in (KEITHLEY_6221_ADDR, SR830_ADDR):
            inst = rm.open_resource(address)
            opened.append(inst)
            inst.timeout = 5000
            inst.write_termination = "\n"
            inst.read_termination = "\n"
        src, lockin = opened
        # The SR830 manual requires OUTX 1 at the start of a GPIB program.
        write(lockin, "OUTX 1")
        verify_identity(src, "6221", KEITHLEY_6221_ADDR)
        verify_identity(lockin, "SR830", SR830_ADDR)
        return rm, src, lockin
    except Exception:
        for inst in opened:
            try:
                inst.close()
            except Exception:
                pass
        rm.close()
        raise


def setup_keithley_6221(src, initial_current_rms_a):
    initial_current_peak_a = rms_to_peak(initial_current_rms_a)

    write(src, "*RST")
    write(src, "*CLS")

    for command in (
        "SOUR:WAVE:FUNC SIN",
        f"SOUR:WAVE:FREQ {SOURCE_FREQ_HZ}",
        f"SOUR:WAVE:AMPL {initial_current_peak_a:.12e}",
        "SOUR:WAVE:OFFS 0",
        "SOUR:WAVE:RANG BEST",
        "SOUR:WAVE:PMAR 0",
        f"SOUR:WAVE:PMAR:OLIN {TRIGGER_LINK_LINE}",
        "SOUR:WAVE:PMAR:STAT ON",
        "SOUR:WAVE:ARM",
        "SOUR:WAVE:INIT",
    ):
        write_6221_checked(src, command)
    marker_enabled = int(query_float(src, "SOUR:WAVE:PMAR:STAT?"))
    marker_line = int(query_float(src, "SOUR:WAVE:PMAR:OLIN?"))
    if marker_enabled != 1 or marker_line != TRIGGER_LINK_LINE:
        raise RuntimeError(
            f"6221 phase marker mismatch: enabled={marker_enabled}, "
            f"line={marker_line}"
        )


def set_6221_current_rms(src, current_rms_a):
    current_peak_a = rms_to_peak(current_rms_a)

    # Restart waveform at each current step to make the amplitude update stable.
    for command in (
        "SOUR:WAVE:ABOR",
        f"SOUR:WAVE:AMPL {current_peak_a:.12e}",
        "SOUR:WAVE:ARM",
        "SOUR:WAVE:INIT",
    ):
        write_6221_checked(src, command)


def read_6221_status(src):
    output_raw = query_text(src, "OUTP?")
    output_on = int(float(output_raw)) == 1
    if not output_on:
        raise RuntimeError("6221 output unexpectedly turned OFF during ramp")
    freq = query_float(src, "SOUR:WAVE:FREQ?")
    ampl_peak_a = query_float(src, "SOUR:WAVE:AMPL?")
    ampl_rms_a = peak_to_rms(ampl_peak_a)
    error = query_text(src, "SYST:ERR?")
    if int(error.split(",", 1)[0].strip()) != 0:
        raise RuntimeError(f"6221 SCPI error during ramp: {error}")
    output_status = "ON"

    return output_status, output_raw, freq, ampl_rms_a, ampl_peak_a, error


def stop_keithley_6221(src):
    errors = []
    for command in ("SOUR:WAVE:ABOR", "CURR 0", "OUTP OFF", "*RST", "*CLS"):
        try:
            write(src, command)
        except Exception as exc:
            errors.append(f"{command}: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))


def setup_sr830(lockin):
    write(lockin, "*CLS")
    for command in (
        "FMOD 0",
        "RSLP 2",  # 6221 Trigger Link phase marker: TTL falling edge
        "ICPL 0",  # AC coupling
        f"HARM {HARMONIC}",
    ):
        write_sr830_checked(lockin, command)

    time.sleep(0.2)

    actual_harmonic = int(query_float(lockin, "HARM?"))
    reference_mode = int(query_float(lockin, "FMOD?"))
    reference_slope = int(query_float(lockin, "RSLP?"))
    if reference_mode != 0 or reference_slope != 2:
        raise RuntimeError(
            f"SR830 reference mismatch: FMOD={reference_mode}, "
            f"RSLP={reference_slope}"
        )
    ref_freq = query_float(lockin, "FREQ?")

    print(f"SR830 harmonic = {actual_harmonic}")
    print(f"SR830 reference frequency = {ref_freq:.6f} Hz")
    print(f"Target demod frequency = {ref_freq * actual_harmonic:.6f} Hz")


def read_sr830(lockin):
    x = query_float(lockin, "OUTP? 1")
    y = query_float(lockin, "OUTP? 2")
    r = query_float(lockin, "OUTP? 3")
    phase = query_float(lockin, "OUTP? 4")
    ref_freq = query_float(lockin, "FREQ?")

    return x, y, r, phase, ref_freq


def generate_ramp_values(start, stop, step):
    values = []

    if step <= 0:
        raise ValueError("STEP_CURRENT_RMS_A must be > 0")

    if stop >= start:
        current = start
        while current < stop:
            values.append(current)
            current += step
        values.append(stop)
    else:
        current = start
        while current > stop:
            values.append(current)
            current -= step
        values.append(stop)

    return values


def main():
    rm, src, lockin = connect_instruments()

    try:
        lockin.clear()
    except Exception:
        pass

    csv_file = None
    csv_writer = None

    if SAVE_CSV:
        csv_path = timestamped_csv_path(CSV_PATH)
        csv_file = csv_path.open("x", newline="", encoding="utf-8")
        print(f"Saving data to {csv_path.resolve()}")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow([
            "timestamp",
            "elapsed_s",
            "step_index",
            "step_elapsed_s",
            "Keithley6221_Output",
            "Keithley6221_Irms_Setpoint_A",
            "Keithley6221_Irms_Readback_A",
            "Keithley6221_Ipeak_Readback_A",
            "Keithley6221_Freq_Hz",
            "Keithley6221_Error",
            "X_V",
            "Y_V",
            "R_V",
            "Phase_deg",
            "SR830_RefFreq_Hz",
            "ActualDemodFreq_Hz",
        ])

    ramp_values = generate_ramp_values(
        START_CURRENT_RMS_A,
        STOP_CURRENT_RMS_A,
        STEP_CURRENT_RMS_A,
    )

    plt.ion()

    fig, axes = plt.subplots(5, 1, sharex=True, figsize=(10, 9))
    ax_r, ax_phase, ax_ref, ax_i, ax_f = axes

    line_r, = ax_r.plot([], [], label="R (V)")
    line_phase, = ax_phase.plot([], [], label="Phase (deg)")
    line_ref, = ax_ref.plot([], [], label="SR830 RefFreq (Hz)")
    line_i, = ax_i.plot([], [], label="6221 I_rms Readback (mA)")
    line_f, = ax_f.plot([], [], label="6221 Freq (Hz)")

    ax_r.set_ylabel("R (V)")
    ax_phase.set_ylabel("Phase (deg)")
    ax_ref.set_ylabel("RefFreq (Hz)")
    ax_i.set_ylabel("I_rms Readback (mA)")
    ax_f.set_ylabel("6221 Freq (Hz)")
    ax_f.set_xlabel("Time (s)")

    for ax in axes:
        ax.grid(True)
        ax.legend(loc="upper right")

    times = []
    rs = []
    phases = []
    ref_freqs = []
    irms_read_ma = []
    source_freqs = []
    stop_requested = False
    shutdown_started = False

    def on_close(_event):
        nonlocal stop_requested, shutdown_started
        stop_requested = True
        if not shutdown_started:
            shutdown_started = True
            try:
                stop_keithley_6221(src)
                print("Plot closed. Keithley 6221 safely stopped.")
            except Exception as exc:
                print(f"Plot-close shutdown warning: {exc}")

    fig.canvas.mpl_connect("close_event", on_close)

    try:
        setup_keithley_6221(src, ramp_values[0])
        setup_sr830(lockin)
        t0 = time.monotonic()

        for step_index, current_rms_a in enumerate(ramp_values):
            if stop_requested:
                break

            set_6221_current_rms(src, current_rms_a)

            step_start = time.monotonic()
            print(
                f"\nStep {step_index + 1}/{len(ramp_values)} | "
                f"I_rms Setpoint = {current_rms_a * 1e3:.3f} mA | "
                f"Measure {STEP_MEASURE_TIME_S:.1f} s"
            )

            while True:
                if stop_requested or not plt.fignum_exists(fig.number):
                    stop_requested = True
                    break

                now = time.monotonic()
                elapsed = now - t0
                step_elapsed = now - step_start

                if step_elapsed > STEP_MEASURE_TIME_S:
                    break

                x, y, r, phase, sr830_ref_freq = read_sr830(lockin)

                (
                    output_status,
                    output_raw,
                    source_freq,
                    current_rms_readback_a,
                    current_peak_readback_a,
                    keithley_error,
                ) = read_6221_status(src)

                actual_demod_freq = sr830_ref_freq * HARMONIC

                times.append(elapsed)
                rs.append(r)
                phases.append(phase)
                ref_freqs.append(sr830_ref_freq)
                irms_read_ma.append(current_rms_readback_a * 1e3)
                source_freqs.append(source_freq)

                print(
                    f"t={elapsed:8.2f}s | "
                    f"step={step_index + 1}, "
                    f"6221 Output={output_status}, "
                    f"I_rms Setpoint={current_rms_a * 1e3:.3f} mA, "
                    f"I_rms Readback={current_rms_readback_a * 1e3:.3f} mA, "
                    f"I_peak Readback={current_peak_readback_a * 1e3:.3f} mA, "
                    f"6221Freq={source_freq:.6f} Hz, "
                    f"6221Err={keithley_error} | "
                    f"X={x:+.6e} V, Y={y:+.6e} V, R={r:+.6e} V, "
                    f"Phase={phase:+.3f} deg, "
                    f"RefFreq={sr830_ref_freq:.6f} Hz, "
                    f"DemodFreq={actual_demod_freq:.6f} Hz"
                )

                if csv_writer:
                    csv_writer.writerow([
                        datetime.now().isoformat(timespec="milliseconds"),
                        f"{elapsed:.6f}",
                        step_index + 1,
                        f"{step_elapsed:.6f}",
                        output_status,
                        f"{current_rms_a:.12e}",
                        f"{current_rms_readback_a:.12e}",
                        f"{current_peak_readback_a:.12e}",
                        f"{source_freq:.9f}",
                        keithley_error,
                        f"{x:.12e}",
                        f"{y:.12e}",
                        f"{r:.12e}",
                        f"{phase:.6f}",
                        f"{sr830_ref_freq:.9f}",
                        f"{actual_demod_freq:.9f}",
                    ])
                    csv_file.flush()

                voltage_scale, voltage_unit = select_voltage_display_unit(rs)
                line_r.set_data(
                    times,
                    [value * voltage_scale for value in rs],
                )
                ax_r.set_ylabel(f"R ({voltage_unit})")
                line_phase.set_data(times, phases)
                line_ref.set_data(times, ref_freqs)
                line_i.set_data(times, irms_read_ma)
                line_f.set_data(times, source_freqs)

                for ax in axes:
                    ax.relim()
                    ax.autoscale_view()

                ax_f.set_xlim(max(0, elapsed - 300), max(60, elapsed))

                fig.suptitle(
                    f"6221 Ramp | "
                    f"I_rms Readback = {current_rms_readback_a * 1e3:.3f} mA | "
                    f"Setpoint = {current_rms_a * 1e3:.3f} mA | "
                    f"6221 Freq = {source_freq:.6f} Hz | "
                    f"SR830 Harmonic = {HARMONIC}"
                )

                plt.tight_layout()
                plt.pause(0.01)

                if stop_requested:
                    break

                sleep_time = max(0.0, SAMPLE_INTERVAL_S - (time.monotonic() - now))
                time.sleep(sleep_time)

        if stop_requested:
            print("\nPlot closed. Ramp measurement stopped.")
        else:
            print("\nRamp measurement finished.")

        stop_keithley_6221(src)
        print("Keithley 6221 waveform stopped and output turned OFF.")

        if not stop_requested and plt.fignum_exists(fig.number):
            plt.ioff()
            plt.show()

    finally:
        try:
            stop_keithley_6221(src)
        except Exception as exc:
            print(f"Failed to confirm Keithley 6221 shutdown: {exc}")

        if csv_file:
            csv_file.close()

        for resource in (src, lockin):
            try:
                resource.close()
            except Exception:
                pass
        rm.close()


if __name__ == "__main__":
    main()
