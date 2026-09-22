import math
import time
import tkinter as tk
from collections import deque
from tkinter import messagebox, ttk

import pyvisa
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


KEITHLEY_2400_ADDRESS = "GPIB0::24::INSTR"

# Fixed safety and measurement settings.
RES_CURRENT_LEVELS_A = [
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
]
RES_TARGET_VOLTAGE_LOW = 0.05
RES_TARGET_VOLTAGE_HIGH = 2.0
RES_SETTLE_TIME_S = 0.2
RES_AVERAGE_COUNT = 5
RES_VOLTAGE_LIMIT_V = 5.0
IV_CURRENT_LIMIT_A = 1e-3
IV_MAX_VOLTAGE_V = 300.0
NPLC = 1.0

DEFAULT_IV_START_V = 0.0
DEFAULT_IV_STOP_V = 5.0
DEFAULT_IV_STEP_V = 0.5
IV_INTERVAL_MS = 1000


def format_resistance(resistance_ohm):
    if not math.isfinite(resistance_ohm):
        return "--"

    abs_r = abs(resistance_ohm)

    if abs_r >= 1e9:
        return f"{resistance_ohm / 1e9:.6g} GOhm"
    if abs_r >= 1e6:
        return f"{resistance_ohm / 1e6:.6g} MOhm"
    if abs_r >= 1e3:
        return f"{resistance_ohm / 1e3:.6g} kOhm"

    return f"{resistance_ohm:.6g} Ohm"


class Keithley2400Controller:
    def __init__(self, address):
        self.rm = pyvisa.ResourceManager()
        self.src = None
        self.closed = False
        self.source_mode = None

        try:
            self.src = self.rm.open_resource(address)
            self.src.timeout = 10000
            self.src.write_termination = "\n"
            self.src.read_termination = "\n"
            identity = self.src.query("*IDN?").strip()
            if "2400" not in identity.upper():
                raise RuntimeError(
                    f"Refusing to control unexpected instrument at {address}: {identity}"
                )
            self.identity = identity
        except Exception:
            if self.src is not None:
                self.src.close()
            self.rm.close()
            self.closed = True
            raise

    def write(self, command, check=True):
        self.src.write(command)
        time.sleep(0.03)
        if check:
            self.check_error(command)

    def check_error(self, command):
        error = self.src.query("SYST:ERR?").strip()
        error_code = int(error.split(",", 1)[0].strip())
        if error_code != 0:
            raise RuntimeError(f"2400 SCPI error after '{command}': {error}")

    def clear_errors(self):
        self.write("*CLS", check=False)
        time.sleep(0.05)
        while True:
            error = self.src.query("SYST:ERR?").strip()
            if int(error.split(",", 1)[0].strip()) == 0:
                break

    def idn(self):
        return self.src.query("*IDN?").strip()

    def initialize_resistance(self, source_current_a):
        self.write("*RST", check=False)
        self.clear_errors()
        self.write(":SOUR:FUNC CURR")
        self.write(":SOUR:CURR:MODE FIXED")
        self.write(f":SOUR:CURR {source_current_a:.12e}")
        self.write(f":SENS:VOLT:PROT {RES_VOLTAGE_LIMIT_V:.12g}")
        self.write(":SENS:FUNC:CONC ON")
        self.write(":SENS:FUNC 'VOLT','CURR'")
        self.write(":SENS:VOLT:RANG:AUTO ON")
        self.write(":SENS:CURR:RANG:AUTO ON")
        self.write(f":SENS:VOLT:NPLC {NPLC:.12g}")
        self.write(f":SENS:CURR:NPLC {NPLC:.12g}")
        self.write(":FORM:ELEM VOLT,CURR")
        self.write(":OUTP ON")
        self.source_mode = "CURR"
        time.sleep(0.2)

    def set_current(self, current_a):
        self.write(f":SOUR:CURR {current_a:.12e}")

    def initialize_iv(self, start_v):
        self.write("*RST", check=False)
        self.clear_errors()
        self.write(":SOUR:FUNC VOLT")
        self.write(":SOUR:VOLT:MODE FIXED")
        self.write(f":SOUR:VOLT {start_v:.12g}")
        self.write(":SENS:FUNC 'CURR'")
        self.write(f":SENS:CURR:PROT {IV_CURRENT_LIMIT_A:.12g}")
        self.write(":SENS:CURR:RANG:AUTO ON")
        self.write(f":SENS:CURR:NPLC {NPLC:.12g}")
        self.write(":FORM:ELEM VOLT,CURR")
        self.write(":OUTP ON")
        self.source_mode = "VOLT"
        time.sleep(0.2)

    def set_voltage(self, voltage_v):
        self.write(f":SOUR:VOLT {voltage_v:.12g}")

    def read_measurement(self):
        response = self.src.query(":READ?").strip()
        parts = response.split(",")
        voltage_v = float(parts[0])
        current_a = float(parts[1])

        if abs(current_a) < 1e-15:
            resistance_ohm = math.nan
        else:
            resistance_ohm = voltage_v / current_a

        return voltage_v, current_a, resistance_ohm

    def stop(self):
        if self.source_mode == "VOLT":
            try:
                self.write(":SOUR:VOLT 0", check=False)
            except Exception:
                pass
        elif self.source_mode == "CURR":
            try:
                self.write(":SOUR:CURR 0", check=False)
            except Exception:
                pass

        self.write(":OUTP OFF", check=False)
        self.source_mode = None

    def close(self):
        if self.closed:
            return

        try:
            self.stop()
        except Exception:
            pass

        # Leave the SourceMeter in a known idle state when the UI exits.
        for command in ("*RST", "*CLS"):
            try:
                self.write(command, check=False)
            except Exception:
                pass

        try:
            self.write(":SYST:LOC", check=False)
        except Exception:
            pass

        try:
            self.src.close()
        finally:
            self.rm.close()
            self.closed = True


class ControlApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Keithley 2400 Control")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.controller = None
        self.running_iv = False
        self.closing = False
        self.t0 = None
        self.iv_points = []
        self.iv_index = 0

        self.iv_voltage = []
        self.iv_current = []
        self.iv_resistance = deque()

        self.iv_start_entry = tk.StringVar(value=f"{DEFAULT_IV_START_V:g}")
        self.iv_stop_entry = tk.StringVar(value=f"{DEFAULT_IV_STOP_V:g}")
        self.iv_step_entry = tk.StringVar(value=f"{DEFAULT_IV_STEP_V:g}")

        self.output_text = tk.StringVar(value="Disconnected")
        self.mode_text = tk.StringVar(value="--")
        self.voltage_text = tk.StringVar(value="-- V")
        self.current_text = tk.StringVar(value="-- A")
        self.resistance_text = tk.StringVar(value="--")
        self.message_text = tk.StringVar(value="Ready")

        self._build_ui()
        self.root.after(50, self.connect)

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=16)
        frame.grid(row=0, column=0)

        iv = ttk.LabelFrame(frame, text="I-V settings", padding=12)
        iv.grid(row=0, column=0, sticky="ew")

        self._add_entry_row(iv, 0, "Start V", self.iv_start_entry, "V")
        self._add_entry_row(iv, 1, "Stop V", self.iv_stop_entry, "V")
        self._add_entry_row(iv, 2, "Step", self.iv_step_entry, "V")

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, pady=12)

        ttk.Button(buttons, text="Measure R", command=self.measure_resistance).grid(row=0, column=0, padx=4)
        ttk.Button(buttons, text="Start I-V", command=self.start_iv).grid(row=0, column=1, padx=4)
        ttk.Button(buttons, text="Stop", command=self.stop_output).grid(row=0, column=2, padx=4)

        readback = ttk.LabelFrame(frame, text="2400 readback", padding=12)
        readback.grid(row=2, column=0, sticky="ew")

        self._add_readback_row(readback, 0, "Output", self.output_text)
        self._add_readback_row(readback, 1, "Mode", self.mode_text)
        self._add_readback_row(readback, 2, "Voltage", self.voltage_text)
        self._add_readback_row(readback, 3, "Current", self.current_text)
        self._add_readback_row(readback, 4, "Resistance", self.resistance_text)

        plot_frame = ttk.LabelFrame(frame, text="Live plot", padding=8)
        plot_frame.grid(row=0, column=1, rowspan=3, padx=(16, 0), sticky="nsew")

        self.figure = Figure(figsize=(6.5, 5.0), dpi=100)
        self.ax_iv = self.figure.add_subplot(211)
        self.ax_r = self.figure.add_subplot(212)
        self.figure.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_frame)
        self.canvas.get_tk_widget().grid(row=0, column=0)

        ttk.Label(
            frame,
            textvariable=self.message_text,
            anchor="w",
            foreground="#555555",
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        self._draw_idle_plots()

    @staticmethod
    def _add_entry_row(parent, row, label, variable, unit):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(0 if row == 0 else 8, 0))
        ttk.Entry(parent, textvariable=variable, width=12, justify="right").grid(
            row=row,
            column=1,
            padx=(12, 4),
            pady=(0 if row == 0 else 8, 0),
        )
        ttk.Label(parent, text=unit).grid(row=row, column=2, sticky="w", pady=(0 if row == 0 else 8, 0))

    @staticmethod
    def _add_readback_row(parent, row, label, variable):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Label(parent, textvariable=variable, width=22, anchor="e").grid(
            row=row,
            column=1,
            padx=(20, 0),
            pady=2,
        )

    def _draw_idle_plots(self):
        self.ax_iv.clear()
        self.ax_iv.set_title("Keithley 2400 I-V Curve")
        self.ax_iv.set_xlabel("Voltage (V)")
        self.ax_iv.set_ylabel("Current (A)")
        self.ax_iv.grid(True)

        self.ax_r.clear()
        self.ax_r.set_title("Sample Resistance vs Voltage")
        self.ax_r.set_xlabel("Voltage (V)")
        self.ax_r.set_ylabel("Resistance (Ohm)")
        self.ax_r.grid(True)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def connect(self):
        if self.controller is not None and not self.controller.closed:
            self.message_text.set("Already connected.")
            return

        try:
            self.controller = Keithley2400Controller(
                KEITHLEY_2400_ADDRESS
            )
            self.message_text.set(f"Connected: {self.controller.identity}")
            self.output_text.set("OFF")
        except Exception as exc:
            self.output_text.set("Connection error")
            self.message_text.set(str(exc))
            messagebox.showerror("Keithley 2400", f"Connection failed:\n{exc}")

    def _ensure_connected(self):
        if self.controller is None or self.controller.closed:
            self.connect()

        if self.controller is None or self.controller.closed:
            raise RuntimeError("Keithley 2400 is not connected.")

    def measure_resistance(self):
        try:
            self._ensure_connected()
            self.stop_output(close_message=False)

            voltage_v, current_a, resistance_ohm, source_current_a = self.measure_resistance_auto_range()
            self._update_readback(voltage_v, current_a, resistance_ohm)
            self.controller.stop()

            self.output_text.set("OFF")
            self.mode_text.set("--")
            self.message_text.set(
                f"Resistance measured: {format_resistance(resistance_ohm)} "
                f"(source {source_current_a:.3e} A)"
            )
        except Exception as exc:
            self.stop_output(close_message=False)
            messagebox.showerror("Measure R failed", str(exc))

    def measure_resistance_auto_range(self):
        current_levels = [level for level in RES_CURRENT_LEVELS_A if level <= IV_CURRENT_LIMIT_A]

        if not current_levels:
            raise RuntimeError("No valid resistance current levels.")

        current_index = 0
        source_current_a = current_levels[current_index]

        self.controller.initialize_resistance(source_current_a)
        self.output_text.set("ON")
        self.mode_text.set("Resistance")
        self.message_text.set("Auto-ranging resistance...")
        self.root.update_idletasks()

        voltage_v = math.nan
        current_a = math.nan
        resistance_ohm = math.nan

        for _ in range(len(current_levels) * 2):
            time.sleep(RES_SETTLE_TIME_S)
            voltage_v, current_a, resistance_ohm = self.controller.read_measurement()
            abs_voltage = abs(voltage_v)

            if abs_voltage > RES_VOLTAGE_LIMIT_V * 0.9 and current_index > 0:
                current_index -= 1
            elif abs_voltage > RES_TARGET_VOLTAGE_HIGH and current_index > 0:
                current_index -= 1
            elif abs_voltage < RES_TARGET_VOLTAGE_LOW and current_index < len(current_levels) - 1:
                current_index += 1
            else:
                break

            source_current_a = current_levels[current_index]
            self.controller.set_current(source_current_a)
            self.message_text.set(f"Auto-ranging resistance... source {source_current_a:.3e} A")
            self.root.update_idletasks()

        readings = []
        for _ in range(RES_AVERAGE_COUNT):
            time.sleep(RES_SETTLE_TIME_S)
            voltage_v, current_a, resistance_ohm = self.controller.read_measurement()
            if math.isfinite(resistance_ohm):
                readings.append((voltage_v, current_a, resistance_ohm))

        if readings:
            voltage_v = sum(item[0] for item in readings) / len(readings)
            current_a = sum(item[1] for item in readings) / len(readings)
            resistance_ohm = sum(item[2] for item in readings) / len(readings)

        return voltage_v, current_a, resistance_ohm, source_current_a

    def _get_iv_values(self):
        start_v = float(self.iv_start_entry.get())
        stop_v = float(self.iv_stop_entry.get())
        step_v = abs(float(self.iv_step_entry.get()))

        if step_v <= 0:
            raise ValueError("I-V step must be > 0 V.")

        start_v = self._limit_voltage(start_v)
        stop_v = self._limit_voltage(stop_v)
        return start_v, stop_v, step_v

    def _limit_voltage(self, voltage_v):
        if voltage_v > IV_MAX_VOLTAGE_V:
            self.message_text.set(f"Voltage limited to +{IV_MAX_VOLTAGE_V:g} V.")
            return IV_MAX_VOLTAGE_V
        if voltage_v < -IV_MAX_VOLTAGE_V:
            self.message_text.set(f"Voltage limited to -{IV_MAX_VOLTAGE_V:g} V.")
            return -IV_MAX_VOLTAGE_V
        return voltage_v

    def _build_voltage_points(self, start_v, stop_v, step_v):
        points = []
        v = start_v

        if start_v <= stop_v:
            while v < stop_v:
                points.append(v)
                v += step_v
        else:
            while v > stop_v:
                points.append(v)
                v -= step_v

        if not points or not math.isclose(points[-1], stop_v, rel_tol=0.0, abs_tol=1e-12):
            points.append(stop_v)

        return points

    def start_iv(self):
        try:
            self._ensure_connected()
            start_v, stop_v, step_v = self._get_iv_values()

            self.stop_output(close_message=False)
            self.iv_points = self._build_voltage_points(start_v, stop_v, step_v)
            self.iv_index = 0
            self.iv_voltage.clear()
            self.iv_current.clear()
            self.iv_resistance.clear()

            self.controller.initialize_iv(start_v)
            self.running_iv = True
            self.t0 = time.time()

            self.output_text.set("ON")
            self.mode_text.set("I-V")
            self.message_text.set("I-V sweep started.")
            self._draw_idle_plots()
            self.root.after(IV_INTERVAL_MS, self.poll_iv)
        except Exception as exc:
            self.stop_output(close_message=False)
            messagebox.showerror("Start I-V failed", str(exc))

    def poll_iv(self):
        if self.closing or not self.running_iv:
            return

        if self.iv_index >= len(self.iv_points):
            self.message_text.set("I-V sweep finished.")
            self.running_iv = False
            self.stop_output(close_message=False)
            self.message_text.set("I-V sweep finished. Output is OFF.")
            return

        try:
            set_voltage_v = self.iv_points[self.iv_index]
            self.controller.set_voltage(set_voltage_v)
            voltage_v, current_a, resistance_ohm = self.controller.read_measurement()
            self.iv_index += 1

            self._update_readback(voltage_v, current_a, resistance_ohm)
            self._show_compliance_warning(current_a)

            self.iv_voltage.append(voltage_v)
            self.iv_current.append(current_a)
            self.iv_resistance.append(resistance_ohm)

            self._plot_iv()
        except Exception as exc:
            self.message_text.set(f"I-V error: {exc}. Stopping output.")
            self.stop_output(close_message=False)
            messagebox.showerror("I-V stopped", f"Measurement failed and output was stopped:\n{exc}")
            return

        self.root.after(IV_INTERVAL_MS, self.poll_iv)

    def _plot_iv(self):
        self.ax_iv.clear()
        self.ax_iv.set_title("Keithley 2400 I-V Curve")
        self.ax_iv.set_xlabel("Voltage (V)")
        self.ax_iv.set_ylabel("Current (A)")
        self.ax_iv.grid(True)
        self.ax_iv.plot(self.iv_voltage, self.iv_current, "o-", linewidth=1.5, markersize=4)
        self._autoscale_xy(self.ax_iv, self.iv_voltage, self.iv_current, 1.0, 1e-12)

        self.ax_r.clear()
        self.ax_r.set_title("Sample Resistance vs Voltage")
        self.ax_r.set_xlabel("Voltage (V)")
        self.ax_r.set_ylabel("Resistance (Ohm)")
        self.ax_r.grid(True)
        self.ax_r.plot(self.iv_voltage, list(self.iv_resistance), "o-", linewidth=1.5, markersize=4)
        self._autoscale_xy(self.ax_r, self.iv_voltage, list(self.iv_resistance), 1.0, 1.0)

        self.figure.tight_layout()
        self.canvas.draw_idle()

    def _update_readback(self, voltage_v, current_a, resistance_ohm):
        self.voltage_text.set(f"{voltage_v:.6e} V")
        self.current_text.set(f"{current_a:.6e} A")
        self.resistance_text.set(format_resistance(resistance_ohm))

    def _show_compliance_warning(self, current_a):
        if abs(current_a) >= IV_CURRENT_LIMIT_A * 0.98:
            self.message_text.set("Current is near compliance limit. Output current is limited.")

    @staticmethod
    def _autoscale_y(axis, values, minimum_margin):
        finite_values = [v for v in values if math.isfinite(v)]
        if not finite_values:
            return

        ymin = min(finite_values)
        ymax = max(finite_values)
        margin = max(abs(ymin) * 0.05, minimum_margin) if ymin == ymax else (ymax - ymin) * 0.15
        axis.set_ylim(ymin - margin, ymax + margin)

    @staticmethod
    def _autoscale_xy(axis, xs, ys, x_min_margin, y_min_margin):
        if xs:
            xmin = min(xs)
            xmax = max(xs)
            x_margin = max(abs(xmin) * 0.05, x_min_margin) if xmin == xmax else (xmax - xmin) * 0.15
            axis.set_xlim(xmin - x_margin, xmax + x_margin)

        finite_y = [v for v in ys if math.isfinite(v)]
        if finite_y:
            ymin = min(finite_y)
            ymax = max(finite_y)
            y_margin = max(abs(ymin) * 0.05, y_min_margin) if ymin == ymax else (ymax - ymin) * 0.15
            axis.set_ylim(ymin - y_margin, ymax + y_margin)

    def stop_output(self, close_message=True):
        self.running_iv = False

        if self.controller is None or self.controller.closed:
            return

        try:
            self.controller.stop()
            self.output_text.set("OFF")
            self.mode_text.set("--")
            if close_message:
                self.message_text.set("Output stopped.")
        except Exception as exc:
            if close_message:
                messagebox.showerror("Stop failed", str(exc))

    def on_close(self):
        if self.closing:
            return

        self.closing = True
        self.message_text.set("Turning output off...")
        self.root.update_idletasks()

        try:
            if self.controller is not None:
                self.controller.close()
        finally:
            self.root.destroy()


def main():
    root = tk.Tk()
    app = None

    try:
        app = ControlApp(root)
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        if app is not None and app.controller is not None:
            app.controller.close()


if __name__ == "__main__":
    main()
