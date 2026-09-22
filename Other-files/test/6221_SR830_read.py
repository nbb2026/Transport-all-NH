import csv
import time
from pathlib import Path
from datetime import datetime
import math

import pyvisa
import matplotlib.pyplot as plt


KEITHLEY_6221_ADDR = "GPIB0::30::INSTR"
SR830_ADDR = "GPIB0::8::INSTR"

CURRENT_RMS_A = 0.1e-3

SOURCE_FREQ_HZ = 13.7
HARMONIC = 2
TRIGGER_LINK_LINE = 4

SAMPLE_INTERVAL_S = 1.0
RUN_TIME_S = 60.0

SAVE_CSV = True
RESULTS_DIRECTORY = (
    Path(__file__).resolve().parent / f"{Path(__file__).stem}_Results"
)
CSV_PATH = RESULTS_DIRECTORY / "sr830_2omega_log.csv"


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


def setup_keithley_6221(src):
    current_peak_a = rms_to_peak(CURRENT_RMS_A)

    write(src, "*RST")
    write(src, "*CLS")
    for command in (
        "SOUR:WAVE:FUNC SIN",
        f"SOUR:WAVE:FREQ {SOURCE_FREQ_HZ}",
        f"SOUR:WAVE:AMPL {current_peak_a:.12e}",
        "SOUR:WAVE:OFFS 0",
        "SOUR:WAVE:RANG BEST",
        "SOUR:WAVE:PMAR 0",
        f"SOUR:WAVE:PMAR:OLIN {TRIGGER_LINK_LINE}",
        "SOUR:WAVE:PMAR:STAT ON",
        "SOUR:WAVE:ARM",
        "SOUR:WAVE:INIT",
    ):
        write_6221_checked(src, command)


def verify_keithley_6221(src):
    func = query_text(src, "SOUR:WAVE:FUNC?")
    freq = query_text(src, "SOUR:WAVE:FREQ?")
    ampl_peak_a = query_float(src, "SOUR:WAVE:AMPL?")
    ampl_rms_a = peak_to_rms(ampl_peak_a)
    offs = query_text(src, "SOUR:WAVE:OFFS?")
    marker_enabled = int(query_float(src, "SOUR:WAVE:PMAR:STAT?"))
    marker_line = int(query_float(src, "SOUR:WAVE:PMAR:OLIN?"))
    if marker_enabled != 1 or marker_line != TRIGGER_LINK_LINE:
        raise RuntimeError(
            f"6221 phase marker mismatch: enabled={marker_enabled}, "
            f"line={marker_line}"
        )

    print(f"6221 wave function = {func}")
    print(f"6221 wave frequency = {freq} Hz")
    print(f"6221 wave amplitude = {ampl_rms_a:.12e} A rms")
    print(f"6221 wave amplitude = {ampl_peak_a:.12e} A peak")
    print(f"6221 wave offset = {offs} A")
    print(f"6221 Trigger Link phase marker = line {marker_line}")


def stop_keithley_6221(src):
    errors = []
    for command in ("SOUR:WAVE:ABOR", "CURR 0", "OUTP OFF", "*RST", "*CLS"):
        try:
            write(src, command)
        except Exception as exc:
            errors.append(f"{command}: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))


def read_keithley_6221_status(src):
    output_raw = query_text(src, "OUTP?")
    output_on = int(float(output_raw)) == 1
    if not output_on:
        raise RuntimeError("6221 output unexpectedly turned OFF during acquisition")
    output_status = "ON"
    current_peak_a = query_float(src, "SOUR:WAVE:AMPL?")
    current_rms_a = peak_to_rms(current_peak_a)
    source_freq_hz = query_float(src, "SOUR:WAVE:FREQ?")

    return output_status, output_raw, current_rms_a, current_peak_a, source_freq_hz


def setup_sr830(lockin):
    write(lockin, "*CLS")
    for command in (
        "FMOD 0",
        "RSLP 2",  # 6221 Trigger Link phase marker: TTL falling edge
        f"HARM {HARMONIC}",
        "ISRC 1",
        "ICPL 0",  # AC coupling
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
    input_source = int(query_float(lockin, "ISRC?"))
    input_coupling = int(query_float(lockin, "ICPL?"))
    unlock = int(query_float(lockin, "LIAS? 3"))

    coupling_text = "DC" if input_coupling == 1 else "AC"
    input_text = {
        0: "A",
        1: "A-B",
        2: "Current 1 MOhm",
        3: "Current 100 MOhm",
    }.get(input_source, f"UNKNOWN({input_source})")
    unlock_text = "UNLOCK" if unlock else "LOCKED"

    print(f"SR830 harmonic = {actual_harmonic}")
    print(f"SR830 input source = {input_text}")
    print(f"SR830 input coupling = {coupling_text}")
    print(f"SR830 lock status = {unlock_text}")
    print(f"SR830 reference frequency = {ref_freq:.6f} Hz")
    print(f"Target demod frequency = {ref_freq * actual_harmonic:.6f} Hz")


def read_sr830(lockin):
    x = query_float(lockin, "OUTP? 1")
    y = query_float(lockin, "OUTP? 2")
    r = query_float(lockin, "OUTP? 3")
    phase = query_float(lockin, "OUTP? 4")
    ref_freq = query_float(lockin, "FREQ?")
    unlock = int(query_float(lockin, "LIAS? 3"))

    return x, y, r, phase, ref_freq, unlock


def main():
    rm = None
    src = None
    lockin = None
    csv_file = None
    csv_writer = None

    try:
        rm = pyvisa.ResourceManager()
        src = rm.open_resource(KEITHLEY_6221_ADDR)
        lockin = rm.open_resource(SR830_ADDR)

        for inst in (src, lockin):
            inst.timeout = 5000
            inst.write_termination = "\n"
            inst.read_termination = "\n"

        # The SR830 manual requires OUTX 1 at the start of a GPIB program.
        write(lockin, "OUTX 1")
        verify_identity(src, "6221", KEITHLEY_6221_ADDR)
        verify_identity(lockin, "SR830", SR830_ADDR)

        try:
            lockin.clear()
        except Exception:
            pass

        if SAVE_CSV:
            csv_path = timestamped_csv_path(CSV_PATH)
            csv_file = csv_path.open("x", newline="", encoding="utf-8")
            print(f"Saving data to {csv_path.resolve()}")
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow([
                "timestamp", "elapsed_s", "remaining_s", "Keithley6221_Output",
                "Keithley6221_OutputRaw", "Keithley6221_Irms_A",
                "Keithley6221_Ipeak_A", "Keithley6221_Freq_Hz", "X_V", "Y_V",
                "R_V", "Phase_deg", "RefFreq_Hz", "ActualDemodFreq_Hz",
                "SR830_Unlock",
            ])

        setup_keithley_6221(src)
        verify_keithley_6221(src)
        setup_sr830(lockin)

        t0 = time.monotonic()
        times, rs, phases, freqs = [], [], [], []
        irms_values_ma = []
        unlock_values = []

        print(f"Acquisition will run for {RUN_TIME_S:.1f} s.")
        plt.ion()
        fig, axes = plt.subplots(5, 1, sharex=True, figsize=(9, 9))
        ax_r, ax_phase, ax_freq, ax_i, ax_unlock = axes
        line_r, = ax_r.plot([], [], label="R (V)")
        line_phase, = ax_phase.plot([], [], label="Phase (deg)")
        line_freq, = ax_freq.plot([], [], label="RefFreq (Hz)")
        line_i, = ax_i.plot([], [], label="6221 I_rms (mA)")
        line_unlock, = ax_unlock.step([], [], where="post", label="SR830 Unlock")

        ax_r.set_ylabel("R (V)")
        ax_phase.set_ylabel("Phase (deg)")
        ax_freq.set_ylabel("RefFreq (Hz)")
        ax_i.set_ylabel("I_rms (mA)")
        ax_unlock.set_ylabel("Unlock")
        ax_unlock.set_xlabel("Time (s)")
        ax_unlock.set_yticks([0, 1])
        ax_unlock.set_yticklabels(["Locked", "Unlock"])
        for ax in axes:
            ax.grid(True)
            ax.legend(loc="upper right")

        plot_enabled = True
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

        while True:
            if stop_requested:
                break

            loop_start = time.monotonic()
            elapsed = loop_start - t0

            if elapsed >= RUN_TIME_S:
                print(f"\nRun time reached: {RUN_TIME_S:.1f} s. Stopping acquisition.")
                break

            x, y, r, phase, ref_freq, unlock = read_sr830(lockin)

            (
                output_status,
                output_raw,
                current_rms_a,
                current_peak_a,
                source_freq_hz,
            ) = read_keithley_6221_status(src)

            elapsed = time.monotonic() - t0
            remaining = max(0.0, RUN_TIME_S - elapsed)
            actual_demod_freq = ref_freq * HARMONIC
            unlock_text = "UNLOCK" if unlock else "LOCKED"

            times.append(elapsed)
            rs.append(r)
            phases.append(phase)
            freqs.append(ref_freq)
            irms_values_ma.append(current_rms_a * 1e3)
            unlock_values.append(unlock)

            print(
                f"t={elapsed:8.2f}s, remaining={remaining:8.2f}s | "
                f"6221 Output={output_status}, "
                f"I_rms={current_rms_a * 1e3:.3f} mA, "
                f"I_peak={current_peak_a * 1e3:.3f} mA, "
                f"6221Freq={source_freq_hz:.6f} Hz | "
                f"SR830={unlock_text}, "
                f"X={x:+.6e} V, Y={y:+.6e} V, R={r:+.6e} V, "
                f"Phase={phase:+.3f} deg, "
                f"RefFreq={ref_freq:.6f} Hz, "
                f"DemodFreq={actual_demod_freq:.6f} Hz"
            )

            if csv_writer:
                csv_writer.writerow([
                    datetime.now().isoformat(timespec="milliseconds"),
                    f"{elapsed:.6f}",
                    f"{remaining:.6f}",
                    output_status,
                    output_raw,
                    f"{current_rms_a:.12e}",
                    f"{current_peak_a:.12e}",
                    f"{source_freq_hz:.9f}",
                    f"{x:.12e}",
                    f"{y:.12e}",
                    f"{r:.12e}",
                    f"{phase:.6f}",
                    f"{ref_freq:.9f}",
                    f"{actual_demod_freq:.9f}",
                    unlock,
                ])
                csv_file.flush()

            if plot_enabled and plt.fignum_exists(fig.number):
                voltage_scale, voltage_unit = select_voltage_display_unit(rs)
                line_r.set_data(
                    times,
                    [value * voltage_scale for value in rs],
                )
                ax_r.set_ylabel(f"R ({voltage_unit})")
                line_phase.set_data(times, phases)
                line_freq.set_data(times, freqs)
                line_i.set_data(times, irms_values_ma)
                line_unlock.set_data(times, unlock_values)

                for ax in axes:
                    ax.relim()
                    ax.autoscale_view()

                ax_unlock.set_ylim(-0.2, 1.2)
                ax_unlock.set_xlim(max(0, elapsed - 120), max(10, elapsed))

                fig.suptitle(
                    f"Elapsed {elapsed:.1f}s | Remaining {remaining:.1f}s\n"
                    f"R={r * voltage_scale:+.6g} {voltage_unit} | "
                    f"Phase={phase:+.3f} deg | "
                    f"RefFreq={ref_freq:.6f} Hz | "
                    f"I_rms={current_rms_a * 1e3:.3f} mA | "
                    f"Unlock={unlock_text}"
                )

                plt.tight_layout()
                fig.canvas.draw()
                fig.canvas.flush_events()
                plt.pause(0.01)

                if stop_requested:
                    break

            elif plot_enabled:
                stop_requested = True
                print("Plot window closed. Stopping acquisition.")
                break

            sleep_time = max(0.0, SAMPLE_INTERVAL_S - (time.monotonic() - loop_start))
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received. Stopping acquisition.")

    finally:
        print("Shutting down...")
        shutdown_confirmed = src is not None

        if src is not None:
            try:
                stop_keithley_6221(src)
                print("Keithley 6221 output turned OFF.")
            except Exception as e:
                shutdown_confirmed = False
                print(f"Failed to turn off Keithley 6221 output: {e}")

        if csv_file:
            csv_file.close()

        for resource in (src, lockin):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
        if rm is not None:
            rm.close()

        if shutdown_confirmed:
            print("Measurement stopped. Output shut down and resources closed.")
        else:
            print("WARNING: resources closed, but 6221 output shutdown was not confirmed.")


if __name__ == "__main__":
    main()
