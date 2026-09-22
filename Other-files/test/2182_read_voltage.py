import time
import csv
import tkinter as tk
from datetime import datetime
from collections import deque
from pathlib import Path
from tkinter import filedialog

import pyvisa
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button


# VISA address of the 2182; change it according to the VISA device list printed by the program
# Common format: GPIB0::7::INSTR
VISA_RESOURCE = "GPIB0::7::INSTR"

# 2182 measurement channel
CHANNEL = 1

SAMPLE_INTERVAL = 1.0
NPLC = 1

# True means use the instrument auto range
# False means write the fixed range VOLTAGE_RANGE to the instrument
AUTO_RANGE = True
VOLTAGE_RANGE = 0.1

WINDOW_SECONDS = 6000

ENABLE_CSV = True
RESULTS_DIRECTORY_NAME_PREFIX = f"{Path(__file__).stem}_Results"


def ask_output_parent_directory():
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    try:
        selected = filedialog.askdirectory(
            parent=root,
            title="Choose folder for Keithley 2182 results",
            initialdir=str(Path(__file__).resolve().parent),
            mustexist=True,
        )
    finally:
        root.destroy()
    if not selected:
        return None
    return Path(selected).expanduser().resolve()


def create_output_file(output_parent_directory):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_directory = Path(output_parent_directory) / (
        f"{RESULTS_DIRECTORY_NAME_PREFIX}_{stamp}"
    )
    results_directory.mkdir(parents=True, exist_ok=False)
    csv_path = results_directory / f"keithley_2182_voltage_{stamp}.csv"
    csv_file = csv_path.open("x", newline="", encoding="utf-8")
    return csv_path, csv_file


def assert_no_2182_error(inst, context):
    response = inst.query("SYST:ERR?").strip()
    error_code = int(response.split(",", 1)[0].strip())
    if error_code != 0:
        raise RuntimeError(f"2182 SCPI error after {context}: {response}")


def write_2182_checked(inst, command):
    inst.write(command)
    assert_no_2182_error(inst, repr(command))


def init_2182(inst):
    # Set the VISA communication timeout, in ms
    inst.timeout = 10000

    # Set the SCPI command terminator
    inst.write_termination = "\n"
    inst.read_termination = "\n"

    # Reset the instrument and clear the error queue
    inst.write("*RST")
    inst.write("*CLS")

    # Set the 2182 to voltage measurement mode
    write_2182_checked(inst, ":SENS:FUNC 'VOLT'")

    # Write the measurement channel to the instrument
    write_2182_checked(inst, f":SENS:CHAN {CHANNEL}")

    # Write the NPLC integration time to the instrument
    # Larger NPLC gives steadier readings but slower speed
    write_2182_checked(inst, f":SENS:VOLT:NPLC {NPLC}")

    # Make :READ? return only the reading so it converts directly to float
    write_2182_checked(inst, ":FORM:ELEM READ")

    if AUTO_RANGE:
        # Write the auto-range setting to the specified channel
        write_2182_checked(
            inst, f":SENS:VOLT:CHAN{CHANNEL}:RANG:AUTO ON"
        )
    else:
        # Write the fixed range to the specified channel, in V
        write_2182_checked(
            inst, f":SENS:VOLT:CHAN{CHANNEL}:RANG {VOLTAGE_RANGE}"
        )

    time.sleep(0.5)


def read_voltage_v(inst):
    # Send the read command :READ? to the 2182
    # The instrument returns the current voltage reading, in V
    response = inst.query(":READ?").strip()
    return float(response)


def main():
    # Create the VISA resource manager used to detect and connect instruments
    rm = pyvisa.ResourceManager()

    # Read all VISA instrument addresses detected by this computer
    print("Available VISA devices:")
    resources = rm.list_resources()

    if not resources:
        print("No VISA devices detected; check NI-VISA, cables, and the instrument address.")
    else:
        for res in resources:
            print(" ", res)

    print(f"\nConnecting: {VISA_RESOURCE}")

    # Open the 2182 using its VISA address
    inst = rm.open_resource(VISA_RESOURCE)

    try:
        identity = inst.query("*IDN?").strip()
        if "2182" not in identity.upper():
            raise RuntimeError(f"Unexpected instrument at {VISA_RESOURCE}: {identity}")
        print(f"Connected: {identity}")
        init_2182(inst)
    except Exception:
        inst.close()
        rm.close()
        raise

    csv_file = None
    writer = None
    csv_path = None
    closed = False
    running = True
    recording = False
    ani = None

    def cleanup():
        nonlocal closed, csv_file, running
        if closed:
            return
        running = False
        if ani is not None and ani.event_source is not None:
            ani.event_source.stop()
        closed = True
        for command in (":ABOR", "*RST", "*CLS"):
            try:
                inst.write(command)
            except Exception:
                pass
        try:
            inst.write(":SYST:LOC")
        except Exception:
            pass
        try:
            inst.close()
        except Exception:
            pass
        if csv_file:
            csv_file.flush()
            csv_file.close()
            csv_file = None
            if csv_path is not None:
                print(f"CSV saved: {csv_path.resolve()}")
        rm.close()
        print("Connection closed.")

    t0 = time.time()
    xs = deque()
    ys_nv = deque()

    fig, ax = plt.subplots()
    line, = ax.plot([], [], linewidth=1.5)

    ax.set_title("Keithley 2182 Real-time Voltage")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Voltage (nV)")
    ax.grid(True)
    fig.subplots_adjust(bottom=0.18)

    value_text = ax.text(
        0.02,
        0.95,
        "",
        transform=ax.transAxes,
        va="top",
        ha="left",
    )

    status_text = fig.text(0.02, 0.04, "PREVIEW — not recorded")
    button_axis = fig.add_axes([0.62, 0.025, 0.30, 0.07])
    record_button = Button(button_axis, "Start Formal Recording")

    def start_formal_recording(_event):
        nonlocal csv_file, csv_path, writer, recording, t0
        if closed or recording or not ENABLE_CSV:
            return
        output_parent_directory = ask_output_parent_directory()
        if output_parent_directory is None:
            status_text.set_text(
                "PREVIEW — folder selection cancelled; not recorded"
            )
            fig.canvas.draw_idle()
            return
        try:
            csv_path, csv_file = create_output_file(
                output_parent_directory
            )
            writer = csv.writer(csv_file)
            writer.writerow(
                ["timestamp", "elapsed_s", "voltage_V", "voltage_nV"]
            )
            csv_file.flush()
        except Exception as exc:
            if csv_file is not None:
                try:
                    csv_file.close()
                except Exception:
                    pass
            csv_file = None
            csv_path = None
            writer = None
            status_text.set_text(f"PREVIEW — output error: {exc}")
            fig.canvas.draw_idle()
            return
        recording = True
        t0 = time.time()
        xs.clear()
        ys_nv.clear()
        line.set_data([], [])
        record_button.label.set_text("Recording...")
        record_button.set_active(False)
        button_axis.set_facecolor("#d9d9d9")
        status_text.set_text("FORMAL RECORDING | waiting for first sample")
        fig.canvas.draw_idle()
        print(f"Formal recording started: {csv_path.resolve()}")

    record_button.on_clicked(start_formal_recording)

    def update(_frame):
        if not running or closed:
            return line, value_text

        elapsed_s = time.time() - t0

        try:
            # Read one voltage from the 2182 each time the plot refreshes
            voltage_v = read_voltage_v(inst)
        except Exception as exc:
            value_text.set_text(f"Read error: {exc}")
            return line, value_text

        voltage_nv = voltage_v * 1e9

        xs.append(elapsed_s)
        ys_nv.append(voltage_nv)

        while xs and elapsed_s - xs[0] > WINDOW_SECONDS:
            xs.popleft()
            ys_nv.popleft()

        line.set_data(xs, ys_nv)

        if xs:
            ax.set_xlim(
                max(0, elapsed_s - WINDOW_SECONDS),
                max(WINDOW_SECONDS, elapsed_s),
            )

        if ys_nv:
            ymin = min(ys_nv)
            ymax = max(ys_nv)

            if ymin == ymax:
                margin = max(abs(ymin) * 0.05, 1.0)
            else:
                margin = (ymax - ymin) * 0.15

            ax.set_ylim(ymin - margin, ymax + margin)

        now = datetime.now().isoformat(timespec="milliseconds")
        value_text.set_text(f"{voltage_nv:.3f} nV")

        if recording and writer is not None and csv_file is not None:
            writer.writerow([
                now,
                f"{elapsed_s:.6f}",
                f"{voltage_v:.12e}",
                f"{voltage_nv:.6f}",
            ])
            csv_file.flush()
            status_text.set_text(
                f"FORMAL RECORDING | {voltage_nv:+.3f} nV"
            )
        else:
            status_text.set_text(
                f"PREVIEW — not recorded | {voltage_nv:+.3f} nV"
            )

        print(
            f"{now} | {elapsed_s:10.3f} s | "
            f"{voltage_v:.12e} V | {voltage_nv:.3f} nV"
        )

        return line, value_text

    def on_close(_event):
        cleanup()

    fig.canvas.mpl_connect("close_event", on_close)

    ani = FuncAnimation(
        fig,
        update,
        interval=int(SAMPLE_INTERVAL * 1000),
        blit=False,
        cache_frame_data=False,
    )

    try:
        plt.show()
    finally:
        cleanup()


if __name__ == "__main__":
    main()
