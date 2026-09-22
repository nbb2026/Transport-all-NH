import math
import time
import tkinter as tk
from tkinter import messagebox, ttk

import pyvisa


KEITHLEY_6221_ADDR = "GPIB0::30::INSTR"

DEFAULT_CURRENT_RMS_MA = 0.1
DEFAULT_DC_CURRENT_MA = 0.0
DEFAULT_FREQUENCY_HZ = 13.7
DEFAULT_MARKER_PHASE_DEG = 0.0
DEFAULT_MARKER_LINE = 4
DEFAULT_COMPLIANCE_V = 50.0
POLL_INTERVAL_MS = 500
MAX_WAVE_CURRENT_PEAK_A = 0.105
MAX_DC_CURRENT_A = 0.105
MAX_COMPLIANCE_V = 105.0


def rms_to_peak(current_rms_a):
    return math.sqrt(2.0) * current_rms_a


def peak_to_rms(current_peak_a):
    return current_peak_a / math.sqrt(2.0)


class Keithley6221Controller:
    def __init__(self, address):
        self.rm = pyvisa.ResourceManager()
        self.src = None
        self.closed = False

        try:
            self.src = self.rm.open_resource(address)
            self.src.timeout = 5000
            self.src.write_termination = "\n"
            self.src.read_termination = "\n"
            identity = self.src.query("*IDN?").strip()
            if "6221" not in identity.upper():
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
        time.sleep(0.05)
        if check:
            self.check_error(command)

    def check_error(self, command):
        error = self.src.query("SYST:ERR?").strip()
        error_code = int(error.split(",", 1)[0].strip())
        if error_code != 0:
            raise RuntimeError(f"6221 SCPI error after '{command}': {error}")

    def clear_errors(self):
        self.write("*CLS", check=False)
        time.sleep(0.05)
        while True:
            error = self.src.query("SYST:ERR?").strip()
            if int(error.split(",", 1)[0].strip()) == 0:
                break

    def query_float(self, command):
        return float(self.src.query(command).strip())

    def initialize(
        self,
        current_rms_a,
        frequency_hz,
        marker_phase_deg,
        marker_line,
        compliance_v,
    ):
        current_peak_a = rms_to_peak(current_rms_a)

        self.clear_errors()
        self.write("*RST")
        self.clear_errors()
        self.write(f"CURR:COMP {compliance_v:.12g}")
        self.write("SOUR:WAVE:FUNC SIN")
        self.write(f"SOUR:WAVE:FREQ {frequency_hz:.12g}")
        self.write(f"SOUR:WAVE:AMPL {current_peak_a:.12e}")
        self.write("SOUR:WAVE:OFFS 0")
        self.write("SOUR:WAVE:RANG BEST")
        self.configure_phase_marker(marker_phase_deg, marker_line)
        self.write("OUTP OFF")

    def configure_phase_marker(self, marker_phase_deg, marker_line):
        # The 6221 emits one fixed 1 us pulse per waveform cycle.
        self.write(f"SOUR:WAVE:PMAR {marker_phase_deg:.12g}")
        self.write(f"SOUR:WAVE:PMAR:OLIN {marker_line}")
        self.write("SOUR:WAVE:PMAR:STAT ON")

    def set_output(
        self,
        current_rms_a,
        frequency_hz,
        marker_phase_deg,
        marker_line,
        compliance_v,
        restart,
    ):
        current_peak_a = rms_to_peak(current_rms_a)

        self.write("SOUR:WAVE:ABOR")
        self.write(f"CURR:COMP {compliance_v:.12g}")
        self.write(f"SOUR:WAVE:FREQ {frequency_hz:.12g}")
        self.write(f"SOUR:WAVE:AMPL {current_peak_a:.12e}")
        self.write("SOUR:WAVE:OFFS 0")
        self.write("SOUR:WAVE:RANG BEST")
        self.configure_phase_marker(marker_phase_deg, marker_line)

        if restart:
            self.start()

    def set_dc_output(self, dc_current_a, compliance_v):
        self.write("SOUR:WAVE:ABOR")
        self.write(f"CURR:COMP {compliance_v:.12g}")
        self.write(f"CURR {dc_current_a:.12e}")
        self.write("OUTP ON")

    def start(self):
        self.write("SOUR:WAVE:ARM")
        self.write("SOUR:WAVE:INIT")

    def stop(self):
        errors = []
        for command in ("SOUR:WAVE:ABOR", "CURR 0", "OUTP OFF"):
            try:
                self.write(command, check=False)
            except Exception as exc:
                errors.append(f"{command}: {exc}")
        if errors:
            raise RuntimeError("6221 shutdown failed: " + "; ".join(errors))

    def read_status(self):
        output_raw = self.src.query("OUTP?").strip()
        output_on = int(float(output_raw)) == 1
        current_peak_a = self.query_float("SOUR:WAVE:AMPL?")
        dc_current_a = self.query_float("CURR?")
        frequency_hz = self.query_float("SOUR:WAVE:FREQ?")
        compliance_v = self.query_float("CURR:COMP?")
        marker_on = int(self.query_float("SOUR:WAVE:PMAR:STAT?")) == 1
        marker_phase_deg = self.query_float("SOUR:WAVE:PMAR?")
        marker_line = int(self.query_float("SOUR:WAVE:PMAR:OLIN?"))

        return {
            "output_on": output_on,
            "current_peak_a": current_peak_a,
            "current_rms_a": peak_to_rms(current_peak_a),
            "dc_current_a": dc_current_a,
            "frequency_hz": frequency_hz,
            "compliance_v": compliance_v,
            "marker_on": marker_on,
            "marker_phase_deg": marker_phase_deg,
            "marker_line": marker_line,
        }

    def close(self):
        if self.closed:
            return

        try:
            self.stop()
        except Exception:
            pass

        # Reset configuration only when the UI is closing, after output is off.
        for command in ("*RST", "*CLS"):
            try:
                self.write(command, check=False)
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
        self.root.title("Keithley 6221 Manual Control")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.controller = None
        self.running = False
        self.closing = False

        self.current_entry = tk.StringVar(value=f"{DEFAULT_CURRENT_RMS_MA:g}")
        self.dc_current_entry = tk.StringVar(value=f"{DEFAULT_DC_CURRENT_MA:g}")
        self.frequency_entry = tk.StringVar(value=f"{DEFAULT_FREQUENCY_HZ:g}")
        self.compliance_entry = tk.StringVar(value=f"{DEFAULT_COMPLIANCE_V:g}")
        self.marker_phase_entry = tk.StringVar(value=f"{DEFAULT_MARKER_PHASE_DEG:g}")
        self.marker_line_entry = tk.StringVar(value=str(DEFAULT_MARKER_LINE))

        self.output_text = tk.StringVar(value="Disconnected")
        self.current_rms_text = tk.StringVar(value="-- mA")
        self.current_peak_text = tk.StringVar(value="-- mA")
        self.dc_current_text = tk.StringVar(value="-- mA")
        self.frequency_text = tk.StringVar(value="-- Hz")
        self.compliance_text = tk.StringVar(value="-- V")
        self.marker_state_text = tk.StringVar(value="--")
        self.marker_phase_text = tk.StringVar(value="-- deg")
        self.marker_line_text = tk.StringVar(value="--")
        self.message_text = tk.StringVar(value="Connecting...")

        self._build_ui()
        self.root.after(50, self.connect)

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=16)
        frame.grid(row=0, column=0)

        settings = ttk.LabelFrame(frame, text="Output settings", padding=12)
        settings.grid(row=0, column=0, sticky="ew")

        ttk.Label(settings, text="Current RMS").grid(row=0, column=0, sticky="w")
        ttk.Entry(
            settings,
            textvariable=self.current_entry,
            width=12,
            justify="right",
        ).grid(row=0, column=1, padx=(12, 4))
        ttk.Label(settings, text="mA").grid(row=0, column=2, sticky="w")

        ttk.Label(settings, text="DC current").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(
            settings,
            textvariable=self.dc_current_entry,
            width=12,
            justify="right",
        ).grid(row=1, column=1, padx=(12, 4), pady=(8, 0))
        ttk.Label(settings, text="mA").grid(row=1, column=2, sticky="w", pady=(8, 0))

        ttk.Label(settings, text="Frequency").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(
            settings,
            textvariable=self.frequency_entry,
            width=12,
            justify="right",
        ).grid(row=2, column=1, padx=(12, 4), pady=(8, 0))
        ttk.Label(settings, text="Hz").grid(row=2, column=2, sticky="w", pady=(8, 0))

        ttk.Label(settings, text="Compliance").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(
            settings,
            textvariable=self.compliance_entry,
            width=12,
            justify="right",
        ).grid(row=3, column=1, padx=(12, 4), pady=(8, 0))
        ttk.Label(settings, text="V").grid(row=3, column=2, sticky="w", pady=(8, 0))

        ttk.Label(settings, text="Marker phase").grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(
            settings,
            textvariable=self.marker_phase_entry,
            width=12,
            justify="right",
        ).grid(row=4, column=1, padx=(12, 4), pady=(8, 0))
        ttk.Label(settings, text="deg").grid(row=4, column=2, sticky="w", pady=(8, 0))

        ttk.Label(settings, text="Marker line").grid(row=5, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(
            settings,
            from_=1,
            to=6,
            textvariable=self.marker_line_entry,
            width=10,
            justify="right",
        ).grid(row=5, column=1, padx=(12, 4), pady=(8, 0))
        ttk.Label(settings, text="Trigger Link").grid(
            row=5,
            column=2,
            sticky="w",
            pady=(8, 0),
        )

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, pady=12)

        self.start_button = ttk.Button(buttons, text="Start", command=self.start_output)
        self.start_button.grid(row=0, column=0, padx=4)

        self.dc_button = ttk.Button(buttons, text="Start DC", command=self.start_dc_output)
        self.dc_button.grid(row=0, column=1, padx=4)

        self.apply_button = ttk.Button(buttons, text="Apply", command=self.apply_output)
        self.apply_button.grid(row=0, column=2, padx=4)

        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop_output)
        self.stop_button.grid(row=0, column=3, padx=4)

        readback = ttk.LabelFrame(frame, text="6221 readback", padding=12)
        readback.grid(row=2, column=0, sticky="ew")

        self._add_readback_row(readback, 0, "Output", self.output_text)
        self._add_readback_row(readback, 1, "Current RMS", self.current_rms_text)
        self._add_readback_row(readback, 2, "Current peak", self.current_peak_text)
        self._add_readback_row(readback, 3, "DC current", self.dc_current_text)
        self._add_readback_row(readback, 4, "Frequency", self.frequency_text)
        self._add_readback_row(readback, 5, "Compliance", self.compliance_text)
        self._add_readback_row(readback, 6, "Phase marker", self.marker_state_text)
        self._add_readback_row(readback, 7, "Marker phase", self.marker_phase_text)
        self._add_readback_row(readback, 8, "Marker line", self.marker_line_text)

        ttk.Label(
            frame,
            textvariable=self.message_text,
            anchor="w",
            foreground="#555555",
        ).grid(row=3, column=0, sticky="ew", pady=(10, 0))

    @staticmethod
    def _add_readback_row(parent, row, label, variable):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Label(parent, textvariable=variable, width=20, anchor="e").grid(
            row=row,
            column=1,
            padx=(20, 0),
            pady=2,
        )

    def connect(self):
        try:
            (
                current_rms_a,
                _dc_current_a,
                frequency_hz,
                compliance_v,
                marker_phase_deg,
                marker_line,
            ) = self._get_requested_values()
            self.controller = Keithley6221Controller(KEITHLEY_6221_ADDR)
            self.controller.initialize(
                current_rms_a,
                frequency_hz,
                marker_phase_deg,
                marker_line,
                compliance_v,
            )
            self.message_text.set(f"Connected: {self.controller.identity}")
            self.poll_status()
        except Exception as exc:
            if self.controller is not None:
                self.controller.close()
                self.controller = None
            self.output_text.set("Connection error")
            self.message_text.set(str(exc))
            messagebox.showerror("Keithley 6221", f"Connection failed:\n{exc}")

    def _get_requested_values(self, require_waveform=True):
        current_rms_ma = float(self.current_entry.get())
        dc_current_ma = float(self.dc_current_entry.get())
        frequency_hz = float(self.frequency_entry.get())
        compliance_v = float(self.compliance_entry.get())
        marker_phase_deg = float(self.marker_phase_entry.get())
        marker_line = int(self.marker_line_entry.get())

        if not all(
            math.isfinite(value)
            for value in (
                current_rms_ma,
                dc_current_ma,
                frequency_hz,
                compliance_v,
                marker_phase_deg,
            )
        ):
            raise ValueError("All numeric settings must be finite.")
        if require_waveform and current_rms_ma <= 0:
            raise ValueError("AC current RMS must be > 0 mA.")
        if not require_waveform and current_rms_ma < 0:
            raise ValueError("Current RMS must be >= 0 mA.")
        if rms_to_peak(current_rms_ma * 1e-3) > MAX_WAVE_CURRENT_PEAK_A:
            raise ValueError(
                f"Current RMS exceeds the 6221 waveform limit "
                f"({peak_to_rms(MAX_WAVE_CURRENT_PEAK_A) * 1e3:.3f} mA RMS)."
            )
        if abs(dc_current_ma * 1e-3) > MAX_DC_CURRENT_A:
            raise ValueError(f"DC current must be within ±{MAX_DC_CURRENT_A * 1e3:.1f} mA.")
        if frequency_hz <= 0:
            raise ValueError("Frequency must be > 0 Hz.")
        if compliance_v <= 0:
            raise ValueError("Compliance must be > 0 V.")
        if compliance_v > MAX_COMPLIANCE_V:
            raise ValueError(f"Compliance must be <= {MAX_COMPLIANCE_V:.1f} V.")
        if not 0 <= marker_phase_deg <= 360:
            raise ValueError("Marker phase must be between 0 and 360 degrees.")
        if not 1 <= marker_line <= 6:
            raise ValueError("Marker line must be between 1 and 6.")
        if marker_line == 2:
            raise ValueError(
                "Marker line 2 normally conflicts with the default trigger output. "
                "Use line 3 unless the trigger routing has been changed."
            )

        return (
            current_rms_ma * 1e-3,
            dc_current_ma * 1e-3,
            frequency_hz,
            compliance_v,
            marker_phase_deg,
            marker_line,
        )

    def start_output(self):
        if self.controller is None:
            return

        try:
            (
                current_rms_a,
                _dc_current_a,
                frequency_hz,
                compliance_v,
                marker_phase_deg,
                marker_line,
            ) = self._get_requested_values()
            self.controller.set_output(
                current_rms_a,
                frequency_hz,
                marker_phase_deg,
                marker_line,
                compliance_v,
                restart=True,
            )
            self.running = True
            self.message_text.set("Waveform and phase marker started.")
            self.poll_status_once()
        except Exception as exc:
            try:
                self.controller.stop()
            except Exception:
                pass
            self.running = False
            messagebox.showerror("Start failed", str(exc))

    def start_dc_output(self):
        if self.controller is None:
            return

        try:
            (
                _current_rms_a,
                dc_current_a,
                _frequency_hz,
                compliance_v,
                _marker_phase_deg,
                _marker_line,
            ) = self._get_requested_values(require_waveform=False)
            self.controller.set_dc_output(dc_current_a, compliance_v)
            self.running = True
            self.message_text.set("DC current output started.")
            self.poll_status_once()
        except Exception as exc:
            try:
                self.controller.stop()
            except Exception:
                pass
            self.running = False
            messagebox.showerror("Start DC failed", str(exc))

    def apply_output(self):
        if self.controller is None:
            return

        try:
            (
                current_rms_a,
                _dc_current_a,
                frequency_hz,
                compliance_v,
                marker_phase_deg,
                marker_line,
            ) = self._get_requested_values()
            self.controller.set_output(
                current_rms_a,
                frequency_hz,
                marker_phase_deg,
                marker_line,
                compliance_v,
                restart=self.running,
            )
            self.message_text.set("Current, frequency, and phase marker updated.")
            self.poll_status_once()
        except Exception as exc:
            try:
                self.controller.stop()
            except Exception:
                pass
            self.running = False
            messagebox.showerror("Apply failed", str(exc))

    def stop_output(self):
        if self.controller is None:
            return

        try:
            self.controller.stop()
            self.running = False
            self.message_text.set("Waveform output stopped.")
            self.poll_status_once()
        except Exception as exc:
            messagebox.showerror("Stop failed", str(exc))

    def poll_status_once(self):
        if self.controller is None or self.controller.closed:
            return

        status = self.controller.read_status()
        self.running = status["output_on"]

        self.output_text.set("ON" if status["output_on"] else "OFF")
        self.current_rms_text.set(f"{status['current_rms_a'] * 1e3:.6f} mA")
        self.current_peak_text.set(f"{status['current_peak_a'] * 1e3:.6f} mA")
        self.dc_current_text.set(f"{status['dc_current_a'] * 1e3:.6f} mA")
        self.frequency_text.set(f"{status['frequency_hz']:.6f} Hz")
        self.compliance_text.set(f"{status['compliance_v']:.6f} V")
        self.marker_state_text.set("ON (1 us pulse)" if status["marker_on"] else "OFF")
        self.marker_phase_text.set(f"{status['marker_phase_deg']:.3f} deg")
        self.marker_line_text.set(f"Trigger Link {status['marker_line']}")

    def poll_status(self):
        if self.closing:
            return

        try:
            self.poll_status_once()
        except Exception as exc:
            self.output_text.set("Read error")
            self.message_text.set(f"Readback error: {exc}")

        self.root.after(POLL_INTERVAL_MS, self.poll_status)

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
