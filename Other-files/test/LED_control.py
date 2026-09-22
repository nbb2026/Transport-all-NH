"""TUOTUO MOKE four-channel LED controller.

The serial settings and command format in this program were recovered from
NI I/O Trace captures of the original LabVIEW program:

    115200 baud, 8 data bits, no parity, 1 stop bit, no flow control
    command terminator: CR (``\r``)
    query response terminator: LF (``\n``)

Install the only third-party dependency with::

    py -m pip install pyserial

Run on the Windows control computer with::

    py LED_control.py

The meanings/units of p, g, d and f have not yet been established.  They are
therefore labelled as raw pulse parameters.  The program reads their existing
values from the controller and preserves them when applying a channel.
"""

from __future__ import annotations

import re
import queue
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Callable

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # The GUI can still open and show installation guidance.
    serial = None
    list_ports = None


DEFAULT_PORT = "COM5"
BAUD_RATE = 115200
LED_CONTROL_API_VERSION = 2
READ_TIMEOUT_S = 10.0
WRITE_TIMEOUT_S = 10.0
COMMAND_DELAY_S = 0.010

# This is deliberately conservative.  Raise it only after confirming the LED
# head and controller ratings.  The trace proved 0, 500 and 1000 were used.
CURRENT_LIMIT = 1000

CHANNELS = (1, 2, 3, 4)
PARAMETERS = ("i", "m", "p", "g", "d", "f")
DEFAULT_PULSE_PARAMETERS = {"p": 5, "g": 0, "d": 5, "f": 0}

MODE_LABEL_TO_VALUE = {
    "Off": 0,
    "Continuous": 1,
    "Pulse": 2,
}
MODE_VALUE_TO_LABEL = {value: label for label, value in MODE_LABEL_TO_VALUE.items()}

COMMAND_RE = re.compile(r"^([imgpdf])([1-4])=(-?\d+|\?)$")
RESPONSE_RE = re.compile(r"^([imgpdf])([1-4])=(-?\d+)$")


class LEDProtocolError(RuntimeError):
    """Raised when the controller returns an unexpected response."""


@dataclass
class ChannelSettings:
    current: int = 0
    mode: int = 0
    p: int = 5
    g: int = 0
    d: int = 5
    f: int = 0

    def as_parameters(self) -> dict[str, int]:
        return {
            "i": self.current,
            "m": self.mode,
            "p": self.p,
            "g": self.g,
            "d": self.d,
            "f": self.f,
        }


class LEDSerialController:
    """Thread-safe pyserial implementation of the recovered LED protocol."""

    def __init__(self, logger: Callable[[str], None] | None = None) -> None:
        self._serial = None
        self._lock = threading.RLock()
        self._logger = logger or (lambda _message: None)

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def port(self) -> str | None:
        if self._serial is None:
            return None
        return self._serial.port

    def connect(self, port: str) -> None:
        if serial is None:
            raise RuntimeError(
                "pyserial is not installed. Run: py -m pip install pyserial"
            )

        port = port.strip()
        if not port:
            raise ValueError("Serial port name cannot be empty")

        with self._lock:
            self.disconnect()

            connection = serial.Serial()
            connection.port = port
            connection.baudrate = BAUD_RATE
            connection.bytesize = serial.EIGHTBITS
            connection.parity = serial.PARITY_NONE
            connection.stopbits = serial.STOPBITS_ONE
            connection.timeout = READ_TIMEOUT_S
            connection.write_timeout = WRITE_TIMEOUT_S
            connection.xonxoff = False
            connection.rtscts = False
            connection.dsrdtr = False

            # Set inactive modem-control lines before opening the FTDI port.
            connection.dtr = False
            connection.rts = False
            try:
                connection.open()
                connection.reset_input_buffer()
                connection.reset_output_buffer()
            except BaseException:
                if connection.is_open:
                    connection.close()
                raise
            self._serial = connection

        self._logger(
            f"Connected {port}: {BAUD_RATE}, 8-N-1, no flow control"
        )

    def disconnect(self) -> None:
        with self._lock:
            connection = self._serial
            self._serial = None
            if connection is not None and connection.is_open:
                connection.close()
                self._logger(f"Disconnected {connection.port}")

    def _require_connection(self):
        if not self.is_connected:
            raise RuntimeError("LED controller is not connected yet")
        return self._serial

    @staticmethod
    def _make_command(parameter: str, channel: int, value: int | str) -> str:
        command = f"{parameter}{channel}={value}"
        if not COMMAND_RE.fullmatch(command):
            raise ValueError(f"Invalid LED command: {command!r}")
        return command

    def _write_locked(self, command: str) -> None:
        connection = self._require_connection()
        payload = (command + "\r").encode("ascii")
        connection.write(payload)
        connection.flush()
        self._logger(f"TX  {command}\\r")
        time.sleep(COMMAND_DELAY_S)

    def set_parameter(self, parameter: str, channel: int, value: int) -> None:
        command = self._make_command(parameter, channel, int(value))
        with self._lock:
            self._write_locked(command)

    def query_parameter(self, parameter: str, channel: int) -> int:
        expected_name = f"{parameter}{channel}"
        command = self._make_command(parameter, channel, "?")

        with self._lock:
            connection = self._require_connection()
            connection.reset_input_buffer()
            self._write_locked(command)
            response_bytes = connection.read_until(b"\n")

        if not response_bytes:
            raise TimeoutError(f"Query {expected_name} timed out")

        try:
            response = response_bytes.decode("ascii").strip("\r\n")
        except UnicodeDecodeError as error:
            raise LEDProtocolError(
                f"Query {expected_name} received non-ASCII data: {response_bytes!r}"
            ) from error

        self._logger(f"RX  {response_bytes!r}")
        match = RESPONSE_RE.fullmatch(response)
        if match is None:
            raise LEDProtocolError(
                f"Query {expected_name} received an unrecognized response: {response!r}"
            )

        returned_name = f"{match.group(1)}{match.group(2)}"
        if returned_name != expected_name:
            raise LEDProtocolError(
                f"Queried {expected_name}, but the controller returned {returned_name}"
            )
        return int(match.group(3))

    def query_channel(self, channel: int) -> ChannelSettings:
        values = {
            parameter: self.query_parameter(parameter, channel)
            for parameter in PARAMETERS
        }
        return ChannelSettings(
            current=values["i"],
            mode=values["m"],
            p=values["p"],
            g=values["g"],
            d=values["d"],
            f=values["f"],
        )

    def query_all(self) -> dict[int, ChannelSettings]:
        return {channel: self.query_channel(channel) for channel in CHANNELS}

    def apply_channel(self, channel: int, settings: ChannelSettings) -> None:
        """Apply one channel in exactly the order seen in the LabVIEW trace."""
        with self._lock:
            for parameter, value in settings.as_parameters().items():
                command = self._make_command(parameter, channel, value)
                self._write_locked(command)

    def apply_all(self, settings: dict[int, ChannelSettings]) -> None:
        with self._lock:
            for channel in CHANNELS:
                channel_settings = settings[channel]
                for parameter, value in channel_settings.as_parameters().items():
                    command = self._make_command(parameter, channel, value)
                    self._write_locked(command)

    def all_off(self) -> None:
        """Quickly force every channel current to zero and disable each output."""
        with self._lock:
            # Zero every current first so all optical output is removed as early
            # as possible, then place every channel in disabled mode.
            for channel in CHANNELS:
                self._write_locked(self._make_command("i", channel, 0))
            for channel in CHANNELS:
                self._write_locked(self._make_command("m", channel, 0))


class LEDControlApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MOKE LED Four-Channel Control")
        self.root.geometry("1120x820")
        self.root.minsize(940, 700)

        self.controller = LEDSerialController(self._thread_log)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="led-io")
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._busy = False
        self._closing = False

        self.port_var = tk.StringVar(value=DEFAULT_PORT)
        self.connection_var = tk.StringVar(value="Not connected")
        self.operation_var = tk.StringVar(value="Ready")
        self.auto_readback_var = tk.BooleanVar(value=True)
        self.raw_edit_var = tk.BooleanVar(value=False)
        self.global_current_var = tk.IntVar(value=0)

        self.channel_vars: dict[int, dict[str, tk.Variable]] = {}
        self.action_widgets: list[tk.Widget] = []
        self.raw_entries: list[tk.Widget] = []

        self._build_ui()
        self._refresh_ports()
        self._set_connected_ui(False)
        self.root.after(20, self._drain_ui_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        outer = ttk.Frame(self.root, padding=12)
        outer.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        outer.rowconfigure(4, weight=1)

        self._build_connection_frame(outer)
        self._build_safety_frame(outer)
        self._build_global_frame(outer)
        self._build_channels_frame(outer)
        self._build_log_frame(outer)

    def _build_connection_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Serial connection", padding=10)
        frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(6, weight=1)

        ttk.Label(frame, text="LED serial port:").grid(row=0, column=0, padx=(0, 4))
        self.port_combo = ttk.Combobox(
            frame, textvariable=self.port_var, width=12, state="normal"
        )
        self.port_combo.grid(row=0, column=1, padx=(0, 6))

        refresh_button = ttk.Button(frame, text="Refresh", command=self._refresh_ports)
        refresh_button.grid(row=0, column=2, padx=(0, 6))

        self.connect_button = ttk.Button(frame, text="Connect", command=self._connect)
        self.connect_button.grid(row=0, column=3, padx=(0, 6))

        self.disconnect_button = ttk.Button(
            frame, text="Disconnect", command=self._disconnect
        )
        self.disconnect_button.grid(row=0, column=4, padx=(0, 12))

        ttk.Label(frame, textvariable=self.connection_var).grid(
            row=0, column=5, padx=(0, 12)
        )
        ttk.Label(frame, textvariable=self.operation_var).grid(
            row=0, column=6, sticky="e"
        )

    def _build_safety_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(0, weight=1)

        warning = (
            f"Safety limit: current is only allowed in 0–{CURRENT_LIMIT}. Start from 0 for the first test; "
            "the units of p/g/d/f are not yet confirmed."
        )
        ttk.Label(frame, text=warning, foreground="#9a4d00").grid(
            row=0, column=0, sticky="w"
        )
        self.emergency_button = tk.Button(
            frame,
            text="Zero all / emergency off",
            command=self._all_off,
            bg="#c62828",
            fg="white",
            activebackground="#8e0000",
            activeforeground="white",
            padx=12,
            pady=4,
        )
        self.emergency_button.grid(row=0, column=1, sticky="e")

    def _build_global_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Global control", padding=10)
        frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(10, weight=1)

        ttk.Label(frame, text="Four-channel current preset:").grid(row=0, column=0, padx=(0, 4))
        column = 1
        for label, value in (("0", 0), ("500", 500), ("1000", 1000), ("Max", CURRENT_LIMIT)):
            button = ttk.Button(
                frame,
                text=label,
                width=7,
                command=lambda selected=value: self._apply_preset(selected),
            )
            button.grid(row=0, column=column, padx=3)
            self.action_widgets.append(button)
            column += 1

        all_continuous = ttk.Button(
            frame, text="All four continuous on", command=self._all_continuous
        )
        all_continuous.grid(row=0, column=column, padx=(12, 3))
        self.action_widgets.append(all_continuous)
        column += 1

        apply_all = ttk.Button(frame, text="Apply all", command=self._apply_all)
        apply_all.grid(row=0, column=column, padx=3)
        self.action_widgets.append(apply_all)
        column += 1

        query_all = ttk.Button(frame, text="Query all", command=self._query_all)
        query_all.grid(row=0, column=column, padx=3)
        self.action_widgets.append(query_all)

        ttk.Label(frame, text="Four-channel continuous current:").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Spinbox(
            frame,
            from_=0,
            to=CURRENT_LIMIT,
            increment=1,
            textvariable=self.global_current_var,
            width=10,
        ).grid(row=1, column=1, sticky="w", pady=(8, 0))
        set_continuous_current = ttk.Button(
            frame,
            text="Set four-channel continuous current",
            command=self._apply_global_continuous_current,
        )
        set_continuous_current.grid(
            row=1, column=2, columnspan=2, sticky="w", padx=(6, 0), pady=(8, 0)
        )
        self.action_widgets.append(set_continuous_current)

        ttk.Checkbutton(
            frame, text="Auto readback after setting", variable=self.auto_readback_var
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            frame,
            text="Allow editing raw pulse parameters",
            variable=self.raw_edit_var,
            command=self._update_raw_entry_state,
        ).grid(row=2, column=3, columnspan=3, sticky="w", pady=(8, 0))

    def _build_channels_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=3, column=0, sticky="nsew", pady=(0, 8))
        for row in range(2):
            frame.rowconfigure(row, weight=1)
        for column in range(2):
            frame.columnconfigure(column, weight=1)

        positions = {1: (0, 0), 2: (0, 1), 3: (1, 0), 4: (1, 1)}
        for channel, (row, column) in positions.items():
            self._build_channel_panel(frame, channel, row, column)

    def _build_channel_panel(
        self, parent: ttk.Frame, channel: int, row: int, column: int
    ) -> None:
        panel = ttk.LabelFrame(parent, text=f"LED channel {channel}", padding=10)
        panel.grid(
            row=row,
            column=column,
            sticky="nsew",
            padx=(0 if column == 0 else 4, 4 if column == 0 else 0),
            pady=(0 if row == 0 else 4, 4 if row == 0 else 0),
        )
        panel.columnconfigure(5, weight=1)

        variables: dict[str, tk.Variable] = {
            "i": tk.IntVar(value=0),
            "m": tk.StringVar(value="Off"),
            "p": tk.IntVar(value=DEFAULT_PULSE_PARAMETERS["p"]),
            "g": tk.IntVar(value=DEFAULT_PULSE_PARAMETERS["g"]),
            "d": tk.IntVar(value=DEFAULT_PULSE_PARAMETERS["d"]),
            "f": tk.IntVar(value=DEFAULT_PULSE_PARAMETERS["f"]),
        }
        self.channel_vars[channel] = variables

        ttk.Label(panel, text="Current:").grid(row=0, column=0, sticky="w")
        current = ttk.Spinbox(
            panel,
            from_=0,
            to=CURRENT_LIMIT,
            increment=1,
            textvariable=variables["i"],
            width=10,
        )
        current.grid(row=0, column=1, padx=(0, 4), sticky="w")
        ttk.Label(panel, text=f"(0–{CURRENT_LIMIT})").grid(
            row=0, column=2, padx=(0, 14), sticky="w"
        )

        ttk.Label(panel, text="Mode:").grid(row=0, column=3, sticky="w")
        mode = ttk.Combobox(
            panel,
            textvariable=variables["m"],
            values=tuple(MODE_LABEL_TO_VALUE),
            state="readonly",
            width=8,
        )
        mode.grid(row=0, column=4, sticky="w")

        ttk.Label(panel, text="Raw pulse parameters:").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(10, 3)
        )
        raw_column = 0
        for parameter in ("p", "g", "d", "f"):
            ttk.Label(panel, text=f"{parameter}=").grid(
                row=2, column=raw_column, sticky="e"
            )
            entry = ttk.Entry(panel, textvariable=variables[parameter], width=7)
            entry.grid(row=2, column=raw_column + 1, padx=(0, 8), sticky="w")
            self.raw_entries.append(entry)
            raw_column += 2

        apply_button = ttk.Button(
            panel,
            text="Apply this channel",
            command=lambda selected=channel: self._apply_channel(selected),
        )
        apply_button.grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.action_widgets.append(apply_button)

        query_button = ttk.Button(
            panel,
            text="Query this channel",
            command=lambda selected=channel: self._query_channel(selected),
        )
        query_button.grid(row=3, column=2, columnspan=2, sticky="w", pady=(12, 0))
        self.action_widgets.append(query_button)

    def _build_log_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Communication log", padding=8)
        frame.grid(row=4, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(frame, height=9, wrap="none", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        clear_button = ttk.Button(frame, text="Clear log", command=self._clear_log)
        clear_button.grid(row=1, column=0, sticky="e", pady=(6, 0))

    def _thread_log(self, message: str) -> None:
        # This method is called by the serial worker.  Tkinter must never be
        # touched here: doing so can crash CPython while OpenCV/PCO releases
        # the GIL in the main thread.  Queue a plain Python callback instead.
        self._queue_ui(lambda text=message: self._append_log(text))

    def _queue_ui(self, callback: Callable[[], None]) -> None:
        self._ui_queue.put(callback)

    def _drain_ui_queue(self) -> None:
        """Execute worker completions on the Tk/main thread."""
        processed = 0
        while processed < 200:
            try:
                callback = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except BaseException as error:
                # Keep the queue pump alive even if an individual UI callback
                # fails.  This print runs on the main thread and is diagnostic.
                print(f"LED UI callback error: {error}")
            processed += 1

        try:
            if self.root.winfo_exists():
                self.root.after(20, self._drain_ui_queue)
        except tk.TclError:
            pass

    def _append_log(self, message: str) -> None:
        if not self.root.winfo_exists():
            return
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _refresh_ports(self) -> None:
        ports: list[str] = []
        if list_ports is not None:
            ports = [port.device for port in list_ports.comports()]
        if DEFAULT_PORT not in ports:
            ports.append(DEFAULT_PORT)
        self.port_combo.configure(values=ports)
        self._append_log(
            "Detected serial ports: " + (", ".join(ports) if ports else "none")
        )

    def _set_connected_ui(self, connected: bool) -> None:
        self.connection_var.set(
            f"Connected: {self.controller.port}" if connected else "Not connected"
        )
        self.connect_button.configure(state="disabled" if connected else "normal")
        self.disconnect_button.configure(state="normal" if connected else "disabled")
        self.port_combo.configure(state="disabled" if connected else "normal")
        self.emergency_button.configure(state="normal" if connected else "disabled")
        self._update_action_states()

    def _update_action_states(self) -> None:
        enabled = self.controller.is_connected and not self._busy
        state = "normal" if enabled else "disabled"
        for widget in self.action_widgets:
            widget.configure(state=state)

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = busy
        self.operation_var.set(label if busy else "Ready")
        self._update_action_states()

    def _run_task(
        self,
        label: str,
        function: Callable[[], object],
        on_success: Callable[[object], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        if self._busy:
            messagebox.showinfo("Device busy", "The previous serial operation has not finished yet")
            return

        self._set_busy(True, label)
        future = self.executor.submit(function)

        def finished() -> None:
            self._set_busy(False)
            try:
                result = future.result()
            except BaseException as error:
                self._append_log(f"Error: {error}")
                if on_error is not None:
                    on_error(error)
                elif not self._closing:
                    messagebox.showerror("LED control error", str(error))
                return
            if on_success is not None:
                on_success(result)

        # Future callbacks execute in the serial worker.  Only enqueue here;
        # _drain_ui_queue will run finished() on the Tk/main thread.
        future.add_done_callback(lambda _future: self._queue_ui(finished))

    def _connect(self) -> None:
        port = self.port_var.get().strip()

        def connect_and_query():
            self.controller.connect(port)
            try:
                return self.controller.query_all(), None
            except BaseException as warning:
                return None, warning

        def connected(result: object) -> None:
            state, warning = result
            self._set_connected_ui(True)
            if state is not None:
                self._load_state_into_ui(state)
                self._append_log("Post-connection status query complete")
            if warning is not None:
                self._append_log(f"Connected, but the initial status query failed: {warning}")
                messagebox.showwarning(
                    "Connected but no status read",
                    "COM5 is open, but the status query failed. Check that the correct serial port is selected "
                    "and whether LabVIEW still occupies COM5.\n\n" + str(warning),
                )

        self._run_task("Connecting and querying…", connect_and_query, connected)

    def _disconnect(self) -> None:
        self._run_task(
            "Disconnecting…",
            self.controller.disconnect,
            lambda _result: self._set_connected_ui(False),
        )

    @staticmethod
    def _validate_raw_value(parameter: str, value: object) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError, tk.TclError) as error:
            raise ValueError(f"{parameter} must be an integer") from error
        if number < 0:
            raise ValueError(f"{parameter} cannot be negative")
        return number

    def _settings_from_ui(self, channel: int) -> ChannelSettings:
        variables = self.channel_vars[channel]
        try:
            current = int(variables["i"].get())
        except (ValueError, tk.TclError) as error:
            raise ValueError(f"Channel {channel} current must be an integer") from error

        if not 0 <= current <= CURRENT_LIMIT:
            raise ValueError(
                f"Channel {channel} current must be within 0–{CURRENT_LIMIT}"
            )

        mode_label = str(variables["m"].get())
        if mode_label not in MODE_LABEL_TO_VALUE:
            raise ValueError(f"Channel {channel} mode is invalid: {mode_label}")

        raw = {
            parameter: self._validate_raw_value(
                f"{parameter} of channel {channel}", variables[parameter].get()
            )
            for parameter in ("p", "g", "d", "f")
        }
        return ChannelSettings(
            current=current,
            mode=MODE_LABEL_TO_VALUE[mode_label],
            p=raw["p"],
            g=raw["g"],
            d=raw["d"],
            f=raw["f"],
        )

    def _all_settings_from_ui(self) -> dict[int, ChannelSettings]:
        return {channel: self._settings_from_ui(channel) for channel in CHANNELS}

    def _load_state_into_ui(self, state: dict[int, ChannelSettings]) -> None:
        for channel, settings in state.items():
            variables = self.channel_vars[channel]
            variables["i"].set(settings.current)
            variables["m"].set(
                MODE_VALUE_TO_LABEL.get(settings.mode, f"Unknown({settings.mode})")
            )
            variables["p"].set(settings.p)
            variables["g"].set(settings.g)
            variables["d"].set(settings.d)
            variables["f"].set(settings.f)

    def _readback_after_write(self, channels: tuple[int, ...], enabled: bool):
        if not enabled:
            return None
        return {channel: self.controller.query_channel(channel) for channel in channels}

    def _apply_channel(self, channel: int) -> None:
        try:
            settings = self._settings_from_ui(channel)
        except ValueError as error:
            messagebox.showerror("Input error", str(error))
            return
        do_readback = bool(self.auto_readback_var.get())

        def apply():
            self.controller.apply_channel(channel, settings)
            return self._readback_after_write((channel,), do_readback)

        def done(state: object) -> None:
            if state:
                self._load_state_into_ui(state)
            self._append_log(f"Channel {channel} setting complete")

        self._run_task(f"Setting channel {channel}…", apply, done)

    def _apply_all(self) -> None:
        try:
            settings = self._all_settings_from_ui()
        except ValueError as error:
            messagebox.showerror("Input error", str(error))
            return
        do_readback = bool(self.auto_readback_var.get())

        if any(setting.mode == 2 for setting in settings.values()):
            if not messagebox.askyesno(
                "Confirm pulse mode",
                "At least one channel will enter pulse mode. The physical units of p/g/d/f are not yet confirmed; "
                "continue?",
            ):
                return

        def apply():
            self.controller.apply_all(settings)
            return self._readback_after_write(CHANNELS, do_readback)

        def done(state: object) -> None:
            if state:
                self._load_state_into_ui(state)
            self._append_log("Four-channel setting complete")

        self._run_task("Setting all four channels…", apply, done)

    def _apply_preset(self, current: int) -> None:
        for variables in self.channel_vars.values():
            variables["i"].set(current)
        self._apply_all()

    def _apply_global_continuous_current(self) -> None:
        try:
            current = int(self.global_current_var.get())
        except (ValueError, tk.TclError):
            messagebox.showerror("Input error", "Four-channel continuous current must be an integer")
            return
        if not 0 <= current <= CURRENT_LIMIT:
            messagebox.showerror(
                "Input error", f"Four-channel continuous current must be within 0–{CURRENT_LIMIT}"
            )
            return
        for variables in self.channel_vars.values():
            variables["i"].set(current)
            variables["m"].set("Continuous")
        self._apply_all()

    def _all_continuous(self) -> None:
        for variables in self.channel_vars.values():
            variables["m"].set("Continuous")
        self._apply_all()

    def _query_channel(self, channel: int) -> None:
        def done(settings: object) -> None:
            self._load_state_into_ui({channel: settings})
            self._append_log(f"Channel {channel} query complete")

        self._run_task(
            f"Querying channel {channel}…",
            lambda: self.controller.query_channel(channel),
            done,
        )

    def _query_all(self) -> None:
        def done(state: object) -> None:
            self._load_state_into_ui(state)
            self._append_log("Four-channel query complete")

        self._run_task("Querying all four channels…", self.controller.query_all, done)

    def _all_off(self) -> None:
        if not self.controller.is_connected:
            return
        if self._busy:
            messagebox.showwarning(
                "Serial operation in progress",
                "The current serial operation has not finished; in an emergency also use the light-source hardware switch.",
            )
            return

        def done(_result: object) -> None:
            for variables in self.channel_vars.values():
                variables["i"].set(0)
                variables["m"].set("Off")
            self._append_log("All four channels zeroed and turned off")

        self._run_task("Zeroing and turning off…", self.controller.all_off, done)

    def _update_raw_entry_state(self) -> None:
        if self.raw_edit_var.get():
            if not messagebox.askyesno(
                "Enable advanced parameter editing",
                "The exact meaning and units of p/g/d/f are not yet confirmed. Wrong values may produce abnormal pulses. "
                "Allow editing anyway?",
            ):
                self.raw_edit_var.set(False)
        state = "normal" if self.raw_edit_var.get() else "disabled"
        for entry in self.raw_entries:
            entry.configure(state=state)

    def _on_window_close(self) -> None:
        if self._closing:
            return

        if self._busy:
            messagebox.showinfo("Please wait", "The serial operation has not finished; close the program later")
            return

        if not self.controller.is_connected:
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.root.destroy()
            return

        should_zero = messagebox.askyesno(
            "Exit LED control",
            "Zero all four channel currents and turn off the output first?\n\nChoosing \"Yes\" is recommended.",
        )
        self._closing = True

        def close_connection():
            try:
                if should_zero:
                    self.controller.all_off()
            finally:
                self.controller.disconnect()

        def finish(_result: object) -> None:
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.root.destroy()

        def finish_after_error(_error: BaseException) -> None:
            try:
                self.controller.disconnect()
            finally:
                self.executor.shutdown(wait=False, cancel_futures=True)
                self.root.destroy()

        self._run_task(
            "Exiting safely…",
            close_connection,
            finish,
            finish_after_error,
        )

    def request_close(self) -> None:
        """Request the same safe shutdown used by the window close button."""
        self._on_window_close()


def main() -> None:
    root = tk.Tk()
    app = LEDControlApp(root)
    app._update_raw_entry_state()

    if serial is None:
        root.after(
            250,
            lambda: messagebox.showerror(
                "pyserial missing",
                "Run this in Windows PowerShell first:\n\n"
                "py -m pip install pyserial",
            ),
        )

    root.mainloop()


if __name__ == "__main__":
    main()
