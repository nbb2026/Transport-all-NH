"""Standalone dual-KEPCO electromagnet and dual-Gauss-meter controller.

This file is deliberately self-contained.  It only needs ``pyserial`` and
does not import any of the other MOKE programs.

Protocol recovered from test/kepeco.nitrace:

* Out-of-plane KEPCO/BOP: COM14; measured matching Gauss meter: COM13
* In-plane KEPCO/BOP: COM12; measured matching Gauss meter: COM15
* All four links: 115200 baud, 8-N-1, no flow control
* Commands end with CR; replies are read through LF
* KEPCO: ``remote``, ``en``, ``T <ampere>``, ``IQ``
* Gauss meter: ``GS``; reply resembles ``Hall = -7.74 GS``

The red zero button sends T 0.000 to both KEPCO channels.  It is a software
zero command, not a hardware output-disable or emergency-power-off command.
The capture does not contain a verified hardware-disable instruction.
"""

from __future__ import annotations

import queue
import re
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Callable

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


BAUD_RATE = 115200
READ_TIMEOUT_S = 0.20
WRITE_TIMEOUT_S = 1.0
REPLY_DEADLINE_S = 1.5
QUIET_TIME_S = 0.08
SOFTWARE_CURRENT_LIMIT_A = 10.0

DEFAULT_PORTS = {
    "CHA_BOP": "COM14",
    "CHA_GAUSS": "COM13",
    "CHB_BOP": "COM12",
    "CHB_GAUSS": "COM15",
}

CHANNEL_LABELS = {
    "CHA": "Out-of-plane",
    "CHB": "In-plane",
}

CURRENT_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*\[A\]", re.IGNORECASE)
FIELD_RE = re.compile(
    r"Hall\s*=\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*GS",
    re.IGNORECASE,
)
CHECKSUM_RE = re.compile(r"<[0-9A-Fa-f]{2}>")


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


class SerialProtocolError(RuntimeError):
    """Raised when a device replies but the expected value is absent."""


class AsciiSerialDevice:
    """Small thread-safe CR-command/LF-reply serial transport."""

    def __init__(
        self,
        port: str,
        label: str,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.port = port
        self.label = label
        self._logger = logger
        self._serial = None
        self._lock = threading.RLock()

    @property
    def is_open(self) -> bool:
        return bool(self._serial is not None and self._serial.is_open)

    def _log(self, message: str) -> None:
        if self._logger is not None:
            self._logger(f"{self.label}: {message}")

    def connect(self) -> None:
        if serial is None:
            raise RuntimeError("pyserial is missing; run: pip install pyserial")
        with self._lock:
            self.close()
            self._serial = serial.Serial(
                port=self.port,
                baudrate=BAUD_RATE,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=READ_TIMEOUT_S,
                write_timeout=WRITE_TIMEOUT_S,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
            self._log(f"Opened {self.port} (115200 / 8N1)")

    def close(self) -> None:
        with self._lock:
            handle = self._serial
            self._serial = None
            if handle is not None:
                try:
                    if handle.is_open:
                        handle.close()
                finally:
                    self._log(f"Closed {self.port}")

    def _require_open(self):
        if not self.is_open:
            raise RuntimeError(f"{self.label} is not connected yet")
        return self._serial

    @staticmethod
    def _decode(raw: bytes) -> str:
        return raw.decode("ascii", errors="replace").strip("\x00\r\n ")

    def exchange(
        self,
        command: str,
        matcher: Callable[[str], object | None] | None = None,
        deadline_s: float = REPLY_DEADLINE_S,
    ):
        """Write one command and optionally wait for a matching reply line."""
        wire = command.rstrip("\r\n").encode("ascii") + b"\r"
        with self._lock:
            handle = self._require_open()
            handle.reset_input_buffer()
            handle.write(wire)
            handle.flush()
            self._log(f"TX {wire!r}")

            if matcher is None:
                self._drain_quiet(handle)
                return None

            deadline = time.monotonic() + deadline_s
            received: list[str] = []
            while time.monotonic() < deadline:
                raw = handle.read_until(b"\n")
                if not raw:
                    continue
                text = self._decode(raw)
                if not text:
                    continue
                received.append(text)
                self._log(f"RX {raw!r}")
                result = matcher(text)
                if result is not None:
                    return result

            summary = " | ".join(received) if received else "<no response>"
            raise SerialProtocolError(
                f"{self.label} did not return the expected data; received: {summary}"
            )

    def _drain_quiet(self, handle) -> list[str]:
        """Collect echoes/prompts briefly so they cannot pollute the next query."""
        lines: list[str] = []
        end = time.monotonic() + QUIET_TIME_S
        while time.monotonic() < end:
            waiting = handle.in_waiting
            if waiting <= 0:
                time.sleep(0.005)
                continue
            raw = handle.read_until(b"\n")
            if raw:
                text = self._decode(raw)
                if text:
                    lines.append(text)
                    self._log(f"RX {raw!r}")
                end = time.monotonic() + QUIET_TIME_S
        return lines


class KepcoBOP(AsciiSerialDevice):
    """KEPCO/BOP ASCII link reconstructed from the NI trace."""

    @staticmethod
    def _current_from_line(line: str) -> float | None:
        match = CURRENT_RE.search(CHECKSUM_RE.sub("", line))
        return float(match.group(1)) if match else None

    def enter_remote(self) -> None:
        self.exchange("remote")

    def enable(self) -> None:
        self.exchange("en")

    def initialize_at_zero(self) -> float:
        # T 0.000 is sent before and after enable.  Every individual command
        # appears in the capture; the pre-enable zero reduces retained-setpoint
        # risk, while the post-enable zero confirms the desired target.
        self.enter_remote()
        self.set_current(0.0)
        self.enable()
        self.set_current(0.0)
        return self.query_current()

    def set_current(self, amperes: float) -> None:
        value = float(amperes)
        if abs(value) > SOFTWARE_CURRENT_LIMIT_A + 1e-12:
            raise ValueError(
                f"Current {value:g} A exceeds the software limit "
                f"±{SOFTWARE_CURRENT_LIMIT_A:g} A"
            )
        self.exchange(f"T {value:.3f}")

    def query_current(self) -> float:
        return float(self.exchange("IQ", self._current_from_line))


class GaussMeter(AsciiSerialDevice):
    """Gauss meter link using the captured GS query."""

    @staticmethod
    def _field_from_line(line: str) -> float | None:
        match = FIELD_RE.search(line)
        return float(match.group(1)) if match else None

    def query_field(self) -> float:
        return float(self.exchange("GS", self._field_from_line))


@dataclass
class MagnetChannel:
    name: str
    bop: KepcoBOP | None = None
    gauss: GaussMeter | None = None

    def close(self) -> None:
        if self.bop is not None:
            self.bop.close()
        if self.gauss is not None:
            self.gauss.close()
        self.bop = None
        self.gauss = None


class KepcoGaussApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MOKE KEPCO Out-of-plane/In-plane Electromagnets + Gaussmeter")
        self.root.geometry("1120x720")
        self.root.minsize(980, 650)

        self.events: queue.Queue[tuple] = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="magnet")
        self.channels = {
            "CHA": MagnetChannel("CHA"),
            "CHB": MagnetChannel("CHB"),
        }
        self.closing = False
        self.poll_inflight = False

        self.status_var = tk.StringVar(value="Not connected")
        self.safety_var = tk.BooleanVar(value=False)
        self.auto_poll_var = tk.BooleanVar(value=True)
        self.poll_interval_var = tk.DoubleVar(value=0.50)
        self.port_vars = {
            key: tk.StringVar(value=value) for key, value in DEFAULT_PORTS.items()
        }
        self.target_vars = {
            "CHA": tk.StringVar(value="0.000"),
            "CHB": tk.StringVar(value="0.000"),
        }
        self.current_vars = {
            "CHA": tk.StringVar(value="-- A"),
            "CHB": tk.StringVar(value="-- A"),
        }
        self.field_vars = {
            "CHA": tk.StringVar(value="-- GS"),
            "CHB": tk.StringVar(value="-- GS"),
        }

        self._build_ui()
        self.refresh_ports()
        self.root.after(40, self._drain_events)
        self.root.after(150, self._poll_timer)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        style.configure("Value.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Status.TLabel", foreground="#176b37")

        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        connection = ttk.LabelFrame(outer, text="Serial connection", padding=8)
        connection.pack(fill="x")
        headers = ("Device", "Default port", "Device", "Default port")
        for column, text in enumerate(headers):
            ttk.Label(connection, text=text).grid(
                row=0, column=column, padx=5, pady=(0, 4), sticky="w"
            )

        ttk.Label(connection, text="Out-of-plane electromagnet KEPCO").grid(row=1, column=0, padx=5)
        self._port_box(connection, "CHA_BOP", 1, 1)
        ttk.Label(connection, text="Out-of-plane gaussmeter").grid(row=1, column=2, padx=5)
        self._port_box(connection, "CHA_GAUSS", 1, 3)
        ttk.Label(connection, text="In-plane electromagnet KEPCO").grid(row=2, column=0, padx=5)
        self._port_box(connection, "CHB_BOP", 2, 1)
        ttk.Label(connection, text="In-plane gaussmeter").grid(row=2, column=2, padx=5)
        self._port_box(connection, "CHB_GAUSS", 2, 3)

        buttons = ttk.Frame(connection)
        buttons.grid(row=1, column=4, rowspan=2, padx=(18, 5), sticky="ns")
        ttk.Button(buttons, text="Refresh ports", command=self.refresh_ports).pack(
            side="left", padx=3
        )
        ttk.Button(buttons, text="Connect all four devices", command=self.connect_all).pack(
            side="left", padx=3
        )
        ttk.Button(buttons, text="Disconnect", command=self.disconnect_all).pack(
            side="left", padx=3
        )
        ttk.Label(connection, textvariable=self.status_var, style="Status.TLabel").grid(
            row=3, column=0, columnspan=5, padx=5, pady=(7, 0), sticky="w"
        )
        connection.columnconfigure(1, weight=1)
        connection.columnconfigure(3, weight=1)

        safety = ttk.LabelFrame(outer, text="Safety and enable", padding=8)
        safety.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(
            safety,
            text="I have confirmed the electromagnet, polarity, cooling, wiring, and personnel safety; allow writing non-zero current",
            variable=self.safety_var,
        ).pack(side="left", padx=4)
        ttk.Button(
            safety,
            text="KEPCO remote/enable and zero",
            command=self.initialize_bops,
        ).pack(side="left", padx=10)
        tk.Button(
            safety,
            text="Zero both channels now (not a power-off)",
            command=self.zero_both,
            bg="#d90018",
            fg="white",
            activebackground="#aa0013",
            activeforeground="white",
            font=("Segoe UI", 10, "bold"),
            padx=10,
        ).pack(side="right", padx=4)

        channels_frame = ttk.Frame(outer)
        channels_frame.pack(fill="x", pady=(8, 0))
        channels_frame.columnconfigure(0, weight=1)
        channels_frame.columnconfigure(1, weight=1)
        self._build_channel(channels_frame, "CHA", 0)
        self._build_channel(channels_frame, "CHB", 1)

        polling = ttk.LabelFrame(outer, text="Readout", padding=8)
        polling.pack(fill="x", pady=(8, 0))
        ttk.Button(polling, text="Read all four values", command=self.read_all).pack(
            side="left", padx=4
        )
        ttk.Checkbutton(
            polling,
            text="Continuous readout",
            variable=self.auto_poll_var,
        ).pack(side="left", padx=(15, 4))
        ttk.Label(polling, text="Interval (s):").pack(side="left", padx=(10, 3))
        ttk.Spinbox(
            polling,
            from_=0.2,
            to=10.0,
            increment=0.1,
            textvariable=self.poll_interval_var,
            width=7,
        ).pack(side="left")
        ttk.Label(
            polling,
            text=(
                "The software limit is fixed at ±10.000 A; "
                "the red button only writes 0 A and does not cut the hardware output."
            ),
            foreground="#a45a00",
        ).pack(side="right", padx=4)

        log_frame = ttk.LabelFrame(outer, text="Communication log", padding=6)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.log_text = tk.Text(
            log_frame,
            height=13,
            wrap="none",
            font=("Consolas", 9),
            state="disabled",
        )
        yscroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        xscroll = ttk.Scrollbar(log_frame, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

    def _port_box(self, parent, key: str, row: int, column: int) -> None:
        box = ttk.Combobox(parent, textvariable=self.port_vars[key], width=14)
        box.grid(row=row, column=column, padx=5, pady=2, sticky="ew")
        setattr(self, f"{key.lower()}_box", box)

    def _build_channel(self, parent, name: str, column: int) -> None:
        channel_label = CHANNEL_LABELS[name]
        port_pair = "COM14 / COM13" if name == "CHA" else "COM12 / COM15"
        frame = ttk.LabelFrame(
            parent,
            text=f"{channel_label} electromagnet and gaussmeter (default {port_pair})",
            padding=12,
        )
        frame.grid(row=0, column=column, padx=(0, 4) if column == 0 else (4, 0), sticky="nsew")

        ttk.Label(frame, text="Target current (A):").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.target_vars[name], width=12).grid(
            row=0, column=1, padx=5, sticky="w"
        )
        ttk.Button(
            frame,
            text="Write current",
            command=lambda channel=name: self.set_channel_current(channel),
        ).grid(row=0, column=2, padx=5)
        ttk.Button(
            frame,
            text="Reverse",
            command=lambda channel=name: self.flip_channel_current(channel),
        ).grid(row=0, column=3, padx=5)
        ttk.Button(
            frame,
            text="Zero this channel",
            command=lambda channel=name: self.zero_channel(channel),
        ).grid(row=0, column=4, padx=5)

        ttk.Label(frame, text="Current readback:").grid(row=1, column=0, pady=(14, 4), sticky="w")
        ttk.Label(frame, textvariable=self.current_vars[name], style="Value.TLabel").grid(
            row=1, column=1, columnspan=4, pady=(14, 4), sticky="w"
        )
        ttk.Label(frame, text="Field reading:").grid(row=2, column=0, pady=4, sticky="w")
        ttk.Label(frame, textvariable=self.field_vars[name], style="Value.TLabel").grid(
            row=2, column=1, columnspan=4, pady=4, sticky="w"
        )
        frame.columnconfigure(1, weight=1)

    def _enqueue_log(self, text: str) -> None:
        self.events.put(("log", f"[{_timestamp()}] {text}"))

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _submit(self, label: str, worker: Callable, success: Callable | None = None) -> None:
        if self.closing:
            return
        future = self.executor.submit(worker)

        def done(completed) -> None:
            try:
                result = completed.result()
            except Exception as error:
                self.events.put(("error", label, error))
            else:
                self.events.put(("success", label, result, success))

        future.add_done_callback(done)

    def _drain_events(self) -> None:
        if self.closing:
            return
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "log":
                    self._append_log(event[1])
                elif kind == "error":
                    _, label, error = event
                    self._append_log(f"[{_timestamp()}] ERROR {label}: {error}")
                    if label == "Auto readout":
                        self.poll_inflight = False
                        self.status_var.set(f"Read failed: {error}")
                    else:
                        messagebox.showerror(label, str(error), parent=self.root)
                elif kind == "success":
                    _, label, result, callback = event
                    self._append_log(f"[{_timestamp()}] {label} done")
                    if callback is not None:
                        callback(result)
        except queue.Empty:
            pass
        self.root.after(40, self._drain_events)

    def refresh_ports(self) -> None:
        ports = []
        if list_ports is not None:
            ports = [item.device for item in list_ports.comports()]
        values = sorted(set(ports + list(DEFAULT_PORTS.values())))
        for key in self.port_vars:
            box = getattr(self, f"{key.lower()}_box")
            box.configure(values=values)
        self._append_log(
            f"[{_timestamp()}] Available ports: {', '.join(ports) if ports else 'none detected (keeping default COM)'}"
        )

    def _selected_ports(self) -> dict[str, str]:
        selected = {key: var.get().strip() for key, var in self.port_vars.items()}
        if any(not value for value in selected.values()):
            raise ValueError("All four serial ports must be filled in")
        if len(set(selected.values())) != 4:
            raise ValueError("The four devices cannot share the same COM port")
        return selected

    def connect_all(self) -> None:
        try:
            ports = self._selected_ports()
        except Exception as error:
            messagebox.showerror("Port settings", str(error), parent=self.root)
            return

        def worker() -> None:
            for channel in self.channels.values():
                channel.close()
            try:
                self.channels["CHA"].bop = KepcoBOP(
                    ports["CHA_BOP"], "Out-of-plane BOP", self._enqueue_log
                )
                self.channels["CHA"].gauss = GaussMeter(
                    ports["CHA_GAUSS"], "Out-of-plane Gauss", self._enqueue_log
                )
                self.channels["CHB"].bop = KepcoBOP(
                    ports["CHB_BOP"], "In-plane BOP", self._enqueue_log
                )
                self.channels["CHB"].gauss = GaussMeter(
                    ports["CHB_GAUSS"], "In-plane Gauss", self._enqueue_log
                )
                for channel in self.channels.values():
                    channel.bop.connect()
                    channel.gauss.connect()
            except Exception:
                for channel in self.channels.values():
                    channel.close()
                raise

        def success(_result) -> None:
            self.status_var.set(
                f"Connected: out-of-plane {ports['CHA_BOP']}/{ports['CHA_GAUSS']}  ·  "
                f"in-plane {ports['CHB_BOP']}/{ports['CHB_GAUSS']} (KEPCO not enabled yet)"
            )
            self.auto_poll_var.set(True)

        self._submit("Connect all four devices", worker, success)

    def disconnect_all(self) -> None:
        self.auto_poll_var.set(False)

        def worker() -> None:
            for channel in self.channels.values():
                channel.close()

        def success(_result) -> None:
            self.status_var.set("Not connected")
            for name in self.channels:
                self.current_vars[name].set("-- A")
                self.field_vars[name].set("-- GS")

        self._submit("Disconnect devices", worker, success)

    def _require_bop(self, name: str) -> KepcoBOP:
        bop = self.channels[name].bop
        if bop is None or not bop.is_open:
            raise RuntimeError(f"{CHANNEL_LABELS[name]} KEPCO/BOP not connected")
        return bop

    def _require_gauss(self, name: str) -> GaussMeter:
        meter = self.channels[name].gauss
        if meter is None or not meter.is_open:
            raise RuntimeError(f"{CHANNEL_LABELS[name]} gaussmeter not connected")
        return meter

    def initialize_bops(self) -> None:
        if not self.safety_var.get():
            messagebox.showwarning(
                "Safety confirmation",
                "Tick the safety confirmation first. Initialization sends remote/en and may make the electromagnet output.",
                parent=self.root,
            )
            return
        self.auto_poll_var.set(False)

        def worker() -> dict[str, float]:
            readings = {}
            for name in ("CHA", "CHB"):
                readings[name] = self._require_bop(name).initialize_at_zero()
            return readings

        def success(readings) -> None:
            for name, value in readings.items():
                self.current_vars[name].set(f"{value:+.3f} A")
                self.target_vars[name].set("0.000")
            self.status_var.set("Both KEPCO channels are now in remote/enabled state; target written as 0 A")
            self.auto_poll_var.set(True)

        self._submit("KEPCO remote/enable and zero", worker, success)

    def _parse_target(self, name: str) -> float:
        try:
            value = float(self.target_vars[name].get().strip())
        except ValueError as error:
            raise ValueError(
                f"{CHANNEL_LABELS[name]} target current is not a valid number"
            ) from error
        if abs(value) > SOFTWARE_CURRENT_LIMIT_A + 1e-12:
            raise ValueError(
                f"{CHANNEL_LABELS[name]} target must be within "
                f"±{SOFTWARE_CURRENT_LIMIT_A:.3f} A"
            )
        if value != 0.0 and not self.safety_var.get():
            raise PermissionError("The safety confirmation must be ticked before writing non-zero current")
        return value

    def set_channel_current(self, name: str) -> None:
        try:
            value = self._parse_target(name)
        except Exception as error:
            messagebox.showerror("Target current", str(error), parent=self.root)
            return

        def worker() -> float:
            bop = self._require_bop(name)
            bop.set_current(value)
            time.sleep(0.08)
            return bop.query_current()

        def success(readback) -> None:
            self.current_vars[name].set(f"{readback:+.3f} A")

        self._submit(
            f"{CHANNEL_LABELS[name]} write {value:+.3f} A", worker, success
        )

    def flip_channel_current(self, name: str) -> None:
        try:
            value = -self._parse_target(name)
        except Exception as error:
            messagebox.showerror("Current reversal", str(error), parent=self.root)
            return
        if abs(value) < 0.0005:
            value = 0.0

        def worker() -> float:
            bop = self._require_bop(name)
            bop.set_current(value)
            time.sleep(0.08)
            return bop.query_current()

        def success(readback) -> None:
            self.target_vars[name].set(f"{value:.3f}")
            self.current_vars[name].set(f"{readback:+.3f} A")
            self.status_var.set(
                f"{CHANNEL_LABELS[name]} reversed to {value:+.3f} A"
            )

        self._submit(
            f"{CHANNEL_LABELS[name]} reverse to {value:+.3f} A", worker, success
        )

    def zero_channel(self, name: str) -> None:
        def worker() -> float:
            bop = self._require_bop(name)
            bop.set_current(0.0)
            time.sleep(0.08)
            return bop.query_current()

        def success(readback) -> None:
            self.target_vars[name].set("0.000")
            self.current_vars[name].set(f"{readback:+.3f} A")

        self._submit(
            f"{CHANNEL_LABELS[name]} zero (not a power-off)", worker, success
        )

    def zero_both(self) -> None:
        # Zero is always allowed and never blocked by the safety checkbox.
        self.auto_poll_var.set(False)

        def worker() -> dict[str, float | None]:
            readings: dict[str, float | None] = {}
            errors = []
            for name in ("CHA", "CHB"):
                try:
                    bop = self._require_bop(name)
                    bop.set_current(0.0)
                    readings[name] = None
                except Exception as error:
                    errors.append(f"{CHANNEL_LABELS[name]}: {error}")
            time.sleep(0.08)
            for name in ("CHA", "CHB"):
                try:
                    readings[name] = self._require_bop(name).query_current()
                except Exception as error:
                    errors.append(f"{CHANNEL_LABELS[name]} readback: {error}")
            if errors:
                raise RuntimeError("；".join(errors))
            return readings

        def success(readings) -> None:
            for name, value in readings.items():
                self.target_vars[name].set("0.000")
                self.current_vars[name].set(
                    "0 A written" if value is None else f"{value:+.3f} A"
                )
            self.status_var.set("0 A sent to both channels; note: this is not a hardware power-off")

        self._submit("Zero both channels (not a power-off)", worker, success)

    def _read_all_worker(self) -> dict[str, tuple[float, float]]:
        result = {}
        for name in ("CHA", "CHB"):
            current = self._require_bop(name).query_current()
            field = self._require_gauss(name).query_field()
            result[name] = (current, field)
        return result

    def _show_readings(self, readings) -> None:
        for name, (current, field) in readings.items():
            self.current_vars[name].set(f"{current:+.3f} A")
            self.field_vars[name].set(f"{field:+.2f} GS")
        self.status_var.set("Readings updated")
        self.poll_inflight = False

    def read_all(self) -> None:
        self._submit("Read all four values", self._read_all_worker, self._show_readings)

    def _poll_timer(self) -> None:
        if self.closing:
            return
        devices_ready = all(
            channel.bop is not None
            and channel.bop.is_open
            and channel.gauss is not None
            and channel.gauss.is_open
            for channel in self.channels.values()
        )
        if self.auto_poll_var.get() and devices_ready and not self.poll_inflight:
            self.poll_inflight = True
            self._submit("Auto readout", self._read_all_worker, self._show_readings)
        try:
            interval_ms = max(200, int(float(self.poll_interval_var.get()) * 1000))
        except (ValueError, tk.TclError):
            interval_ms = 500
        self.root.after(interval_ms, self._poll_timer)

    def _on_close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.auto_poll_var.set(False)
        # Closing a serial handle aborts pending reads.  No output command is
        # sent automatically here: an unexpected GUI close must not masquerade
        # as a verified zero.  Use the red button before closing when required.
        for channel in self.channels.values():
            try:
                channel.close()
            except Exception:
                pass
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main() -> None:
    if serial is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Missing dependency",
            "pyserial not found. Run first:\n\npython -m pip install pyserial",
            parent=root,
        )
        root.destroy()
        return
    root = tk.Tk()
    KepcoGaussApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
