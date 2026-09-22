# sr830_realtime_plot.py
import time
from collections import deque

import pyvisa
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation


# VISA address of the SR830.
# GPIB0::8::INSTR means: GPIB0 bus, instrument address 8.
# If the address is unknown, run list_visa_resources() to see the VISA devices detected by this computer.
SR830_ADDRESS = "GPIB0::8::INSTR"

# Read once every 0.5 s, corresponding to a nominal sampling rate of 2 Hz.
SAMPLE_INTERVAL = 0.5
MAX_POINTS = 500


def select_voltage_display_unit(values_v):
    max_abs_v = max((abs(value) for value in values_v), default=0.0)
    if max_abs_v >= 1e-1:
        return 1.0, "V"
    if max_abs_v >= 1e-4:
        return 1e3, "mV"
    if max_abs_v >= 1e-7:
        return 1e6, "uV"
    return 1e9, "nV"


def list_visa_resources():
    rm = pyvisa.ResourceManager()

    # List all instrument addresses this computer can detect through VISA.
    # For example you may see: GPIB0::8::INSTR, USB0::...::INSTR, etc.
    resources = rm.list_resources()

    print("Detected VISA resources:")
    for res in resources:
        print(" ", res)

    rm.close()


def connect_sr830():
    rm = pyvisa.ResourceManager()

    # Open the instrument connection using SR830_ADDRESS.
    sr830 = rm.open_resource(SR830_ADDRESS)

    # Set the instrument communication timeout, in ms.
    sr830.timeout = 5000

    # SR830 GPIB/serial commands usually end with a newline.
    sr830.write_termination = "\n"
    sr830.read_termination = "\n"

    # The SR830 manual requires GPIB programs to first select the response interface with OUTX 1.
    sr830.write("OUTX 1")

    # *IDN?: read the instrument identity to confirm the connected device is an SR830.
    identity = sr830.query("*IDN?").strip()
    if "SR830" not in identity.upper():
        sr830.close()
        rm.close()
        raise RuntimeError(f"Unexpected instrument at {SR830_ADDRESS}: {identity}")
    print("Connected:", identity)

    return rm, sr830


def write_sr830_checked(sr830, command):
    """Send one command and check SR830 command/execution status bits."""
    sr830.write(command)
    event_status = int(sr830.query("*ESR?").strip())
    if event_status & 0x30:
        raise RuntimeError(
            f"SR830 rejected {command!r}; *ESR? returned {event_status}"
        )


def init_sr830(sr830):
    # sr830.write(...) writes a setting command to the instrument.
    # These commands directly change the corresponding parameters on the SR830 front panel.

    sr830.write("*CLS")

    # FMOD: reference source.
    # 0 = external reference, 1 = internal reference.
    write_sr830_checked(sr830, "FMOD 0")

    # In this project the external reference comes from the 6221 Trigger Link Phase Marker (TTL pulse).
    # 2 = TTL falling edge; 0 (analog SINE zero crossing) must not be used.
    write_sr830_checked(sr830, "RSLP 2")
    reference_mode = int(float(sr830.query("FMOD?").strip()))
    reference_slope = int(float(sr830.query("RSLP?").strip()))
    if reference_mode != 0 or reference_slope != 2:
        raise RuntimeError(
            "SR830 external-reference readback mismatch: "
            f"FMOD={reference_mode}, RSLP={reference_slope}"
        )

    # ISRC: signal input configuration.
    # 0 = A，1 = A-B，2 = I(1 MOhm)，3 = I(100 MOhm)。
    write_sr830_checked(sr830, "ISRC 1")

    # IGND: input shield grounding.
    # 0 = Float，1 = Ground。
    write_sr830_checked(sr830, "IGND 0")

    # ICPL: input coupling.
    # 0 = AC，1 = DC。
    write_sr830_checked(sr830, "ICPL 0")

    # ILIN: line-frequency notch filter.
    # 0 = Off，1 = Line，2 = 2xLine，3 = Line + 2xLine。
    write_sr830_checked(sr830, "ILIN 1")

    # SENS: sensitivity, i.e. the input full scale.
    # 0=2 nV/fA, 1=5 nV/fA, 2=10 nV/fA, 3=20 nV/fA
    # 4=50 nV/fA, 5=100 nV/fA, 6=200 nV/fA, 7=500 nV/fA
    # 8=1 uV/pA, 9=2 uV/pA, 10=5 uV/pA, 11=10 uV/pA
    # 12=20 uV/pA, 13=50 uV/pA, 14=100 uV/pA, 15=200 uV/pA
    # 16=500 uV/pA, 17=1 mV/nA, 18=2 mV/nA, 19=5 mV/nA
    # 20=10 mV/nA, 21=20 mV/nA, 22=50 mV/nA, 23=100 mV/nA
    # 24=200 mV/nA, 25=500 mV/nA, 26=1 V/uA。
    write_sr830_checked(sr830, "SENS 9")

    # OFLT: time constant.
    # 0=10 us, 1=30 us, 2=100 us, 3=300 us
    # 4=1 ms, 5=3 ms, 6=10 ms, 7=30 ms
    # 8=100 ms, 9=300 ms, 10=1 s, 11=3 s
    # 12=10 s, 13=30 s, 14=100 s, 15=300 s
    # 16=1 ks, 17=3 ks, 18=10 ks, 19=30 ks。
    write_sr830_checked(sr830, "OFLT 10")

    # OFSL: low-pass filter slope.
    # 0 = 6 dB/oct，1 = 12 dB/oct，2 = 18 dB/oct，3 = 24 dB/oct。
    write_sr830_checked(sr830, "OFSL 1")

    # RMOD: dynamic reserve.
    # 0 = High Reserve，1 = Normal，2 = Low Noise。
    write_sr830_checked(sr830, "RMOD 1")

    # PHAS: reference phase, in degrees.
    write_sr830_checked(sr830, "PHAS 0")


def parse_lia_status(status):
    # LIAS? returns the SR830 status word.
    # Binary bits are used here to determine whether the instrument is overloaded or the reference is unlocked.
    status = int(status)

    # bit 0: input or reserve overload.
    input_overload = bool(status & (1 << 0))

    # bit 1: filter overload.
    filter_overload = bool(status & (1 << 1))

    # bit 2: output overload.
    output_overload = bool(status & (1 << 2))

    # bit 3: reference signal unlock.
    reference_unlock = bool(status & (1 << 3))

    overload = input_overload or filter_overload or output_overload

    messages = []
    if input_overload:
        messages.append("INPUT/RESERVE")
    if filter_overload:
        messages.append("FILTER")
    if output_overload:
        messages.append("OUTPUT")
    if reference_unlock:
        messages.append("UNLOCK")

    if not messages:
        messages.append("OK")

    return overload, ", ".join(messages)


def read_sr830(sr830):
    # sr830.query(...) sends a query command to the instrument and reads the returned value.

    # SNAP? 3,4: read R and Theta in one call.
    # 3 = R，4 = Theta。
    # The SR830 returns a format like: 1.234E-6,45.67
    data = sr830.query("SNAP? 3,4")
    r, theta = map(float, data.strip().split(","))

    # FREQ?: read the current reference frequency.
    # With internal reference it returns the set frequency; with external reference it returns the detected external reference frequency.
    freq = float(sr830.query("FREQ?").strip())

    # LIAS?: read the SR830 status word, used to determine overload, unlock, and other instrument states.
    lia_status = int(sr830.query("LIAS?").strip())
    overload, status_text = parse_lia_status(lia_status)

    return r, theta, freq, overload, status_text


def main():
    # If the instrument address is unknown, uncomment the next line first to list the available VISA addresses.
    # list_visa_resources()

    rm, sr830 = connect_sr830()
    try:
        init_sr830(sr830)
    except Exception:
        sr830.close()
        rm.close()
        raise

    t_data = deque(maxlen=MAX_POINTS)
    r_data = deque(maxlen=MAX_POINTS)
    theta_data = deque(maxlen=MAX_POINTS)

    start_time = time.time()

    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    ax1, ax2 = axes

    line_r, = ax1.plot([], [], label="R", color="tab:blue")
    line_theta, = ax2.plot([], [], label="Theta", color="tab:red")

    freq_text = ax1.text(
        0.02,
        0.95,
        "Reference Frequency: -- Hz",
        transform=ax1.transAxes,
        va="top",
        ha="left",
    )

    overload_text = ax1.text(
        0.02,
        0.87,
        "Status: --",
        transform=ax1.transAxes,
        va="top",
        ha="left",
        color="green",
        fontweight="bold",
    )

    ax1.set_ylabel("R / V")
    ax2.set_ylabel("Theta / degree")
    ax2.set_xlabel("Time / s")

    ax1.grid(True)
    ax2.grid(True)

    ax1.legend(loc="upper right")
    ax2.legend(loc="upper right")

    running = True
    connection_closed = False
    ani = None

    def close_connection():
        """Stop callbacks and close VISA resources exactly once."""
        nonlocal connection_closed
        if connection_closed:
            return

        connection_closed = True
        try:
            sr830.close()
        except Exception as e:
            print("SR830 close warning:", e)
        finally:
            try:
                rm.close()
            except Exception as e:
                print("VISA ResourceManager close warning:", e)

    def stop_reading():
        """Prevent any new SR830 query after the plot starts closing."""
        nonlocal running
        was_running = running
        running = False
        if ani is not None and ani.event_source is not None:
            ani.event_source.stop()
        if was_running:
            print("SR830 data polling stopped.")

    def on_close(_event):
        stop_reading()
        close_connection()

    fig.canvas.mpl_connect("close_event", on_close)

    def update(frame):
        # close_event may run before a queued animation callback. Never access
        # the instrument once shutdown has started.
        if not running or connection_closed:
            return line_r, line_theta, freq_text, overload_text

        try:
            # Each time the plot refreshes, read R, Theta, reference frequency, and status from the SR830 once.
            r, theta, freq, overload, status_text = read_sr830(sr830)

        except Exception as e:
            if not running or connection_closed:
                return line_r, line_theta, freq_text, overload_text
            # Common causes: wrong GPIB address, instrument not powered on, VISA driver problem, communication timeout.
            print("Read error:", e)
            return line_r, line_theta, freq_text, overload_text

        t = time.time() - start_time

        t_data.append(t)
        r_data.append(r)
        theta_data.append(theta)

        voltage_scale, voltage_unit = select_voltage_display_unit(r_data)
        line_r.set_data(
            t_data,
            [value * voltage_scale for value in r_data],
        )
        ax1.set_ylabel(f"R / {voltage_unit}")
        line_theta.set_data(t_data, theta_data)

        freq_text.set_text(f"Reference Frequency: {freq:.6g} Hz")

        if overload:
            overload_text.set_text(f"Status: OVERLOAD ({status_text})")
            overload_text.set_color("red")
        else:
            overload_text.set_text(f"Status: {status_text}")
            overload_text.set_color("green")

        ax1.relim()
        ax1.autoscale_view()

        ax2.relim()
        ax2.autoscale_view()

        print(
            f"t = {t:.2f} s, "
            f"R = {r:.6e} V, "
            f"Theta = {theta:.3f} deg, "
            f"Freq = {freq:.6g} Hz, "
            f"Status = {status_text}"
        )

        return line_r, line_theta, freq_text, overload_text

    ani = FuncAnimation(
        fig,
        update,
        interval=int(SAMPLE_INTERVAL * 1000),
        blit=False,
    )

    try:
        plt.tight_layout()
        plt.show()
    finally:
        stop_reading()
        close_connection()


if __name__ == "__main__":
    main()
