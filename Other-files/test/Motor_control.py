#!/usr/bin/env python3
"""Standalone TUOTUO MOKE cell-stage controller reconstructed from NI traces.

The protocol mapping in this file is based on the operator-labelled capture
``test/Motor_COM4_2.nitrace``:

    Init ON -> Home -> Down 2000 um x 6 -> absolute 7250 um
    -> Home -> Init OFF -> stop LabVIEW

Confirmed mapping
-----------------
* COM4, 115200 baud, 8-N-1, Modbus RTU, slave address 1.
* Init ON: register 0 = 0x0101, 0x0301, 0x17FF.
* Init OFF: register 0 = 0x1700, 0x0100.
* Home: write the captured slow profile and special float target -123.500778;
  poll input registers 4-5 until register 5 becomes 1; then send the Init ON
  sequence, register 0 = 0x0200, and restore the normal profile.
* Speed/acceleration/deceleration scale: displayed value x 51200.
* Step scale: displayed micrometres x 25.6, accumulated as signed int32.
* Absolute-position scale: displayed micrometres x 102.4, signed int32.
* All 32-bit values use the low 16-bit word first.

Input registers 4 and 5 are status words, not encoder position.  This program
therefore displays them separately and never presents them as measured motion.

Dependency: pyserial (``python -m pip install pyserial``).
No other local Python file is imported.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import queue
import struct
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Callable, Optional, Tuple

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


APP_TITLE = "MOKE Cell-VSDM Motor Control"
DEFAULT_PORT = "COM4"
BAUDRATE = 115200
SLAVE_ADDRESS = 1
SERIAL_TIMEOUT_S = 1.0
COMMAND_GAP_S = 0.05
PORT_SETTLE_S = 0.10
WRITE_DRAIN_S = 0.03
LABVIEW_REOPEN_EACH_FRAME = True
POLL_INTERVAL_S = 0.30
HOME_POLL_INTERVAL_S = 0.30
HOME_TIMEOUT_S = 180.0

PROFILE_SCALE = 51_200.0
STEP_SCALE_COUNTS_PER_UM = 25.6
ABS_SCALE_COUNTS_PER_UM = 102.4
HOME_SPEED_RPS = 1.0
HOME_TARGET_FLOAT = -123.500778
MODE1_CODE = 8

MAX_SPEED_RPS = 20.0
MAX_ACCEL_RPS2 = 50.0
MAX_POSITION_UM = 100_000.0
MAX_STEP_UM = 20_000.0


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def append_crc(payload: bytes) -> bytes:
    crc = crc16_modbus(payload)
    return payload + bytes((crc & 0xFF, crc >> 8))


def check_crc(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    received = frame[-2] | (frame[-1] << 8)
    return received == crc16_modbus(frame[:-2])


def build_read_input_registers(start: int, quantity: int) -> bytes:
    return append_crc(struct.pack(">BBHH", SLAVE_ADDRESS, 0x04, start, quantity))


def build_write_single(register: int, value: int) -> bytes:
    return append_crc(struct.pack(">BBHH", SLAVE_ADDRESS, 0x06, register, value))


def build_write_multiple(register: int, data: bytes) -> bytes:
    if not data or len(data) % 2:
        raise ValueError("Register data must be a non-empty even number of bytes")
    quantity = len(data) // 2
    payload = struct.pack(">BBHHB", SLAVE_ADDRESS, 0x10, register, quantity, len(data))
    return append_crc(payload + data)


def encode_float32_low_word_first(value: float) -> bytes:
    normal = struct.pack(">f", float(value))
    return normal[2:4] + normal[0:2]


def encode_int32_low_word_first(value: int) -> bytes:
    unsigned = int(value) & 0xFFFFFFFF
    return struct.pack(">HH", unsigned & 0xFFFF, unsigned >> 16)


def hex_frame(frame: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in frame)


def profile_raw(displayed_value: float) -> float:
    return displayed_value * PROFILE_SCALE


def step_um_to_counts(step_um: float) -> int:
    return int(round(step_um * STEP_SCALE_COUNTS_PER_UM))


def absolute_um_to_counts(position_um: float) -> int:
    return int(round(position_um * ABS_SCALE_COUNTS_PER_UM))


class MotorProtocolError(RuntimeError):
    pass


class HomeCancelled(RuntimeError):
    pass


class MotorController:
    """Thread-safe trace-confirmed Modbus operations."""

    def __init__(
        self,
        logger: Callable[[str], None],
        reopen_each_frame: bool = LABVIEW_REOPEN_EACH_FRAME,
    ):
        self._serial = None
        self._port_name = None
        self._reopen_each_frame = reopen_each_frame
        self._lock = threading.RLock()
        self._logger = logger

    @property
    def connected(self) -> bool:
        return self._port_name is not None

    @property
    def port(self) -> Optional[str]:
        return self._port_name

    def _open_transport_locked(self):
        if self._port_name is None:
            raise MotorProtocolError("Serial port not connected")
        if self._serial is not None and self._serial.is_open:
            return self._serial
        self._serial = serial.Serial(
            port=self._port_name,
            baudrate=BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=SERIAL_TIMEOUT_S,
            write_timeout=SERIAL_TIMEOUT_S,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        # NI-VISA performs viOpen plus a full attribute setup before every
        # transfer.  This pause also lets CH340 DTR/RTS settle after open().
        time.sleep(PORT_SETTLE_S)
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        return self._serial

    def _close_transport_locked(self) -> None:
        if self._serial is None:
            return
        try:
            if self._serial.is_open:
                self._serial.close()
        finally:
            self._serial = None

    def connect(self, port: str) -> None:
        if serial is None:
            raise RuntimeError("pyserial is missing; run: python -m pip install pyserial")
        with self._lock:
            self.disconnect()
            self._port_name = port
            try:
                self._open_transport_locked()
            except Exception:
                self._port_name = None
                self._close_transport_locked()
                raise
            if self._reopen_each_frame:
                self._close_transport_locked()
            mode = "LabVIEW-compatible (reopen serial port per frame)" if self._reopen_each_frame else "keep serial port open"
            self._logger(f"Connected {port}: 115200 8N1, Modbus address 1, {mode}")

    def disconnect(self) -> None:
        with self._lock:
            if self._port_name is None:
                return
            port = self._port_name
            self._close_transport_locked()
            self._port_name = None
            if port:
                self._logger(f"Disconnected {port}")

    def _require_serial(self):
        if not self.connected:
            raise MotorProtocolError("Serial port not connected")
        return self._open_transport_locked()

    def _write_no_reply(self, frame: bytes) -> None:
        with self._lock:
            ser = self._require_serial()
            try:
                ser.reset_input_buffer()
                written = ser.write(frame)
                ser.flush()
                time.sleep(WRITE_DRAIN_S)
                if written != len(frame):
                    raise MotorProtocolError(f"Only {written}/{len(frame)} bytes written")
                self._logger(f"TX  {hex_frame(frame)}")
            finally:
                if self._reopen_each_frame:
                    self._close_transport_locked()

    def _read_response(self) -> bytes:
        ser = self._require_serial()
        header = ser.read(3)
        if len(header) != 3:
            raise MotorProtocolError("Read timeout: complete Modbus response header not received")
        function = header[1]
        tail_size = 2 if function & 0x80 else header[2] + 2
        tail = ser.read(tail_size)
        frame = header + tail
        expected = 5 if function & 0x80 else 3 + header[2] + 2
        if len(frame) != expected:
            raise MotorProtocolError(f"Response length {len(frame)}, expected {expected}")
        self._logger(f"RX  {hex_frame(frame)}")
        if not check_crc(frame):
            raise MotorProtocolError("Response CRC check failed")
        if frame[0] != SLAVE_ADDRESS:
            raise MotorProtocolError(f"Wrong device address in response: {frame[0]}")
        if function & 0x80:
            raise MotorProtocolError(
                f"Modbus exception: function 0x{function & 0x7F:02X}, code 0x{frame[2]:02X}"
            )
        return frame

    def read_status(self) -> Tuple[int, int]:
        request = build_read_input_registers(0x0004, 2)
        with self._lock:
            ser = self._require_serial()
            try:
                ser.reset_input_buffer()
                if ser.write(request) != len(request):
                    raise MotorProtocolError("Status query write incomplete")
                ser.flush()
                self._logger(f"TX  {hex_frame(request)}")
                response = self._read_response()
            finally:
                if self._reopen_each_frame:
                    self._close_transport_locked()
        if response[1] != 0x04 or response[2] != 4:
            raise MotorProtocolError("Wrong function code or length in status response")
        return struct.unpack(">HH", response[3:7])

    def _write_float(self, register: int, value: float) -> None:
        frame = build_write_multiple(register, encode_float32_low_word_first(value))
        self._write_no_reply(frame)

    def _write_int32(self, register: int, value: int) -> None:
        frame = build_write_multiple(register, encode_int32_low_word_first(value))
        self._write_no_reply(frame)

    def _write_control_values(self, values) -> None:
        with self._lock:
            for value in values:
                self._write_no_reply(build_write_single(0x0000, value))
                time.sleep(COMMAND_GAP_S)

    def init_on(self) -> None:
        self._write_control_values((0x0101, 0x0301, 0x17FF))
        self._logger("Init ON complete: 0x0101 → 0x0301 → 0x17FF")

    def init_off(self) -> None:
        self._write_control_values((0x1700, 0x0100))
        self._logger("Init OFF complete: 0x1700 → 0x0100")

    def zero_after_home(self) -> None:
        self._write_control_values((0x0101, 0x0301, 0x17FF, 0x0200))
        self._logger("Home clear/complete sequence sent")

    def apply_profile(
        self, speed_rps: float, acceleration_rps2: float, deceleration_rps2: float
    ) -> None:
        raw_speed = profile_raw(speed_rps)
        raw_accel = profile_raw(acceleration_rps2)
        raw_decel = profile_raw(deceleration_rps2)
        with self._lock:
            self._write_no_reply(build_write_single(0x0022, MODE1_CODE))
            time.sleep(COMMAND_GAP_S)
            self._write_float(0x0003, raw_speed)
            time.sleep(COMMAND_GAP_S)
            self._write_float(0x0005, raw_accel)
            time.sleep(COMMAND_GAP_S)
            self._write_float(0x0007, raw_decel)
        self._logger(
            f"Parameters: Mode1, speed {speed_rps:g} rps, acceleration {acceleration_rps2:g}, "
            f"deceleration {deceleration_rps2:g}"
        )

    def init_and_apply_profile(
        self, speed_rps: float, acceleration_rps2: float, deceleration_rps2: float
    ) -> None:
        self.init_on()
        self.apply_profile(speed_rps, acceleration_rps2, deceleration_rps2)

    def write_target_counts(self, counts: int, description: str) -> None:
        with self._lock:
            self._write_int32(0x0001, counts)
        self._logger(f"Target {description} → raw counts {counts}")

    def home(
        self,
        speed_rps: float,
        acceleration_rps2: float,
        deceleration_rps2: float,
        cancel_event: threading.Event,
        status_callback: Callable[[int, int], None],
    ) -> None:
        # The labelled trace always kept the same acceleration/deceleration and
        # changed only the homing speed from 5 rps to 1 rps.
        self.apply_profile(HOME_SPEED_RPS, acceleration_rps2, deceleration_rps2)
        with self._lock:
            self._write_float(0x0001, HOME_TARGET_FLOAT)
        self._logger(f"Home search started: special target {HOME_TARGET_FLOAT:g}")

        deadline = time.monotonic() + HOME_TIMEOUT_S
        while True:
            if cancel_event.is_set():
                raise HomeCancelled("Home cancelled by the operator")
            if time.monotonic() >= deadline:
                raise MotorProtocolError(f"Home completion flag not detected after {HOME_TIMEOUT_S:g} seconds")
            try:
                status_word, home_flag = self.read_status()
            except MotorProtocolError as exc:
                self._logger(f"HOME POLL  {exc}")
                cancel_event.wait(HOME_POLL_INTERVAL_S)
                continue
            status_callback(status_word, home_flag)
            if home_flag == 1:
                break
            cancel_event.wait(HOME_POLL_INTERVAL_S)

        if cancel_event.is_set():
            raise HomeCancelled("Home cancelled by the operator")
        self._logger("Home completion flag detected: status register 5 = 1")
        self.zero_after_home()
        self.apply_profile(speed_rps, acceleration_rps2, deceleration_rps2)


class MotorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1020x800")
        self.root.minsize(900, 700)

        self.ui_queue = queue.Queue()
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="motor-command"
        )
        self.controller = MotorController(self.thread_log)
        self.poll_stop = threading.Event()
        self.poll_pause = threading.Event()
        self.home_cancel = threading.Event()
        self.poll_thread = None
        self.closing = False
        self.command_busy = False
        self.init_off_busy = False
        self.initialized = False
        self.homed = False
        self.command_counts = 0
        self.pending_target = None

        self.port_var = tk.StringVar(value=DEFAULT_PORT)
        self.connection_var = tk.StringVar(value="Not connected")
        self.init_state_var = tk.StringVar(value="Init OFF")
        self.poll_var = tk.BooleanVar(value=True)
        self.status_word_var = tk.StringVar(value="0x----")
        self.home_flag_var = tk.StringVar(value="—")
        self.command_position_var = tk.StringVar(value="Not homed")
        self.speed_var = tk.StringVar(value="5.000")
        self.accel_var = tk.StringVar(value="10.000")
        self.decel_var = tk.StringVar(value="10.000")
        self.absolute_var = tk.StringVar(value="0.000")
        self.step_var = tk.StringVar(value="0.000")
        self.safety_var = tk.BooleanVar(value=False)
        self.bottom_status_var = tk.StringVar(value="Ready; connect, Init ON, then Home in order")
        self.action_widgets = []

        self._build_ui()
        self.refresh_ports()
        self._update_controls()
        self.root.after(50, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(6, weight=1)

        serial_box = ttk.LabelFrame(outer, text="Serial connection", padding=8)
        serial_box.grid(row=0, column=0, sticky="ew")
        serial_box.columnconfigure(8, weight=1)
        ttk.Label(serial_box, text="Port:").grid(row=0, column=0)
        self.port_combo = ttk.Combobox(
            serial_box, textvariable=self.port_var, width=15, state="normal"
        )
        self.port_combo.grid(row=0, column=1, padx=4)
        ttk.Button(serial_box, text="Refresh", command=self.refresh_ports).grid(row=0, column=2, padx=4)
        self.connect_button = ttk.Button(serial_box, text="Connect", command=self.connect)
        self.connect_button.grid(row=0, column=3, padx=4)
        self.disconnect_button = ttk.Button(serial_box, text="Disconnect", command=self.disconnect)
        self.disconnect_button.grid(row=0, column=4, padx=4)
        ttk.Label(serial_box, textvariable=self.connection_var).grid(row=0, column=5, padx=12)
        ttk.Label(serial_box, textvariable=self.init_state_var).grid(row=0, column=6, padx=12)
        ttk.Label(
            serial_box,
            text="115200 / 8N1 / address 1 / LabVIEW-compatible: reopen per frame",
        ).grid(row=0, column=8, sticky="e")

        safety = ttk.Frame(outer, padding=(2, 9))
        safety.grid(row=1, column=0, sticky="ew")
        safety.columnconfigure(0, weight=1)
        ttk.Label(
            safety,
            text="Safety: the status register is not a measured position. Confirm limits, direction, and mechanical path before moving, and keep the hardware emergency stop within reach.",
            foreground="#a34b00",
        ).grid(row=0, column=0, sticky="w")
        self.init_on_button = ttk.Button(safety, text="Init ON", command=self.init_on)
        self.init_on_button.grid(row=0, column=1, padx=5)
        self.init_off_button = tk.Button(
            safety,
            text="Init OFF / stop output",
            command=self.init_off,
            bg="#d70018",
            fg="white",
            activebackground="#a80012",
            activeforeground="white",
            font=("TkDefaultFont", 10, "bold"),
            padx=14,
            pady=4,
        )
        self.init_off_button.grid(row=0, column=2, padx=(5, 0))

        status_box = ttk.LabelFrame(outer, text="Device status (read-only, not position)", padding=10)
        status_box.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        status_box.columnconfigure(6, weight=1)
        ttk.Label(status_box, text="Register 4:").grid(row=0, column=0)
        ttk.Label(status_box, textvariable=self.status_word_var, font=("TkDefaultFont", 15, "bold")).grid(row=0, column=1, padx=6)
        ttk.Label(status_box, text="Home flag (register 5):").grid(row=0, column=2, padx=(16, 0))
        ttk.Label(status_box, textvariable=self.home_flag_var, font=("TkDefaultFont", 15, "bold")).grid(row=0, column=3, padx=6)
        self.read_button = ttk.Button(status_box, text="Read once", command=self.read_status_once)
        self.read_button.grid(row=0, column=4, padx=8)
        ttk.Checkbutton(status_box, text="Continuous readout", variable=self.poll_var, command=self.poll_changed).grid(row=0, column=5)
        profile_box = ttk.LabelFrame(outer, text="Cell-VSDM parameters", padding=10)
        profile_box.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(profile_box, text="Direction: Mode1").grid(row=0, column=0, padx=(0, 14))
        ttk.Label(profile_box, text="Speed (rps):").grid(row=0, column=1)
        ttk.Entry(profile_box, textvariable=self.speed_var, width=12).grid(row=0, column=2, padx=(4, 16))
        ttk.Label(profile_box, text="Acceleration (rps/s):").grid(row=0, column=3)
        ttk.Entry(profile_box, textvariable=self.accel_var, width=12).grid(row=0, column=4, padx=(4, 16))
        ttk.Label(profile_box, text="Deceleration (rps/s):").grid(row=0, column=5)
        ttk.Entry(profile_box, textvariable=self.decel_var, width=12).grid(row=0, column=6, padx=(4, 16))
        self.apply_button = ttk.Button(profile_box, text="Apply parameters", command=self.apply_profile)
        self.apply_button.grid(row=0, column=7)
        self.action_widgets.append(self.apply_button)

        motion_box = ttk.LabelFrame(outer, text="Position and step (µm)", padding=10)
        motion_box.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        motion_box.columnconfigure(8, weight=1)
        ttk.Label(motion_box, text="Absolute position:").grid(row=0, column=0)
        ttk.Entry(motion_box, textvariable=self.absolute_var, width=14).grid(row=0, column=1, padx=5)
        absolute_buttons = ttk.Frame(motion_box)
        absolute_buttons.grid(row=0, column=2, padx=(0, 20))
        self.goto_button = ttk.Button(
            absolute_buttons, text="Go to absolute position", command=self.goto_absolute
        )
        self.goto_button.pack(side="left")
        self.preset_7250_button = ttk.Button(
            absolute_buttons, text="7250 µm", command=self.goto_preset_7250
        )
        self.preset_7250_button.pack(side="left", padx=(5, 0))
        ttk.Label(motion_box, text="Step:").grid(row=0, column=3)
        ttk.Entry(motion_box, textvariable=self.step_var, width=14).grid(row=0, column=4, padx=5)
        self.up_button = ttk.Button(motion_box, text="↑ Up", command=lambda: self.step_move(-1))
        self.up_button.grid(row=0, column=5, padx=4)
        self.down_button = ttk.Button(motion_box, text="↓ Down", command=lambda: self.step_move(1))
        self.down_button.grid(row=0, column=6, padx=4)
        self.home_button = ttk.Button(motion_box, text="Home", command=self.home)
        self.home_button.grid(row=0, column=7, padx=(18, 0))
        self.action_widgets.extend(
            [
                self.goto_button,
                self.preset_7250_button,
                self.up_button,
                self.down_button,
                self.home_button,
            ]
        )

        ttk.Label(motion_box, text="Software-recorded command position:").grid(row=1, column=0, pady=(10, 0), sticky="w")
        ttk.Label(motion_box, textvariable=self.command_position_var, font=("TkDefaultFont", 12, "bold")).grid(row=1, column=1, columnspan=3, pady=(10, 0), sticky="w")
        ttk.Checkbutton(
            motion_box,
            text="I have confirmed the mechanical path, limits, and direction are safe",
            variable=self.safety_var,
        ).grid(row=1, column=4, columnspan=4, pady=(10, 0), sticky="e")
        ttk.Label(
            motion_box,
            text="Captured scale: absolute position ×102.4; step increment ×25.6. The software command position is not encoder feedback.",
            foreground="#a34b00",
        ).grid(row=2, column=0, columnspan=9, sticky="w", pady=(8, 0))

        progress_box = ttk.Frame(outer)
        progress_box.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        progress_box.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_box, mode="indeterminate")
        self.progress.grid(row=0, column=0, sticky="ew")

        log_box = ttk.LabelFrame(outer, text="Communication log", padding=6)
        log_box.grid(row=6, column=0, sticky="nsew")
        log_box.rowconfigure(0, weight=1)
        log_box.columnconfigure(0, weight=1)
        self.log_text = scrolledtext.ScrolledText(log_box, height=14, wrap="none", font=("TkFixedFont", 10))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.configure(state="disabled")

        ttk.Label(outer, textvariable=self.bottom_status_var, relief="sunken", anchor="w").grid(row=7, column=0, sticky="ew", pady=(8, 0))

    def log(self, text: str) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {text}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def thread_log(self, text: str) -> None:
        self.ui_queue.put(("log", text))

    def _drain_queue(self) -> None:
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.log(item[1])
                elif kind == "status_words":
                    self._show_status_words(item[1], item[2])
                elif kind == "poll_error":
                    self.bottom_status_var.set(item[1])
                elif kind == "task_done":
                    self._finish_task(item[1], item[2], item[3])
                elif kind == "connect_done":
                    self._finish_connect(item[1], item[2])
                elif kind == "disconnect_done":
                    self._finish_disconnect(item[1])
                elif kind == "init_off_done":
                    self._finish_init_off(item[1])
        except queue.Empty:
            pass
        if not self.closing:
            self.root.after(50, self._drain_queue)

    def _show_status_words(self, status_word: int, home_flag: int) -> None:
        self.status_word_var.set(f"0x{status_word:04X}")
        self.home_flag_var.set(f"{home_flag}  ({'triggered' if home_flag == 1 else 'not triggered'})")

    def refresh_ports(self) -> None:
        if list_ports is None:
            self.port_combo["values"] = (DEFAULT_PORT,)
            return
        ports = [p.device for p in list_ports.comports()]
        if DEFAULT_PORT not in ports:
            ports.insert(0, DEFAULT_PORT)
        self.port_combo["values"] = ports

    def _update_controls(self) -> None:
        connected = self.controller.connected
        idle = not self.command_busy and not self.init_off_busy
        self.connect_button.configure(state="normal" if not connected and idle else "disabled")
        self.disconnect_button.configure(state="normal" if connected and idle else "disabled")
        self.port_combo.configure(state="disabled" if connected or not idle else "normal")
        self.read_button.configure(state="normal" if connected and idle else "disabled")
        self.init_on_button.configure(state="normal" if connected and idle else "disabled")
        self.init_off_button.configure(
            state="normal" if connected and not self.init_off_busy else "disabled"
        )
        for widget in self.action_widgets:
            allowed = connected and self.initialized and idle
            if widget is self.home_button:
                allowed = connected and self.initialized and idle
            elif widget in (
                self.goto_button,
                self.preset_7250_button,
                self.up_button,
                self.down_button,
            ):
                allowed = allowed and self.homed
            widget.configure(state="normal" if allowed else "disabled")

    def _set_busy(self, busy: bool, text: Optional[str] = None) -> None:
        self.command_busy = busy
        if text:
            self.bottom_status_var.set(text)
        if busy:
            self.progress.start(10)
        else:
            self.progress.stop()
        self._update_controls()

    def _submit(self, description: str, function, success: str, tag: str = "generic") -> None:
        if self.command_busy:
            return
        self._set_busy(True, description)
        future = self.executor.submit(function)

        def done(completed):
            self.ui_queue.put(("task_done", success, completed.exception(), tag))

        future.add_done_callback(done)

    def _finish_task(self, success: str, error: Optional[BaseException], tag: str) -> None:
        self.poll_pause.clear()
        self._set_busy(False)
        if error is not None:
            if tag == "target":
                self.pending_target = None
            self.bottom_status_var.set(f"Error: {error}")
            self.log(f"ERROR  {type(error).__name__}: {error}")
            if not isinstance(error, HomeCancelled):
                messagebox.showerror(APP_TITLE, str(error), parent=self.root)
            return
        if tag == "init_on":
            self.initialized = True
            self.homed = False
            self.init_state_var.set("Init ON")
            self.command_position_var.set("Not homed")
        elif tag == "home":
            self.homed = True
            self.command_counts = 0
            self.command_position_var.set("Home = 0 µm (command reference)")
        elif tag == "target" and self.pending_target is not None:
            self.command_counts, display_text = self.pending_target
            self.command_position_var.set(display_text)
            self.pending_target = None
        self.bottom_status_var.set(success)
        self._update_controls()

    def connect(self) -> None:
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning(APP_TITLE, "Enter the serial port name", parent=self.root)
            return
        self._set_busy(True, f"Connecting {port}…")
        future = self.executor.submit(lambda: self.controller.connect(port))
        future.add_done_callback(
            lambda f: self.ui_queue.put(("connect_done", port, f.exception()))
        )

    def _finish_connect(self, port: str, error: Optional[BaseException]) -> None:
        self._set_busy(False)
        if error:
            self.bottom_status_var.set(f"Connection failed: {error}")
            messagebox.showerror(APP_TITLE, str(error), parent=self.root)
            return
        self.connection_var.set(f"Connected: {port}")
        self.bottom_status_var.set("Connected; Init not sent yet")
        self.initialized = False
        self.homed = False
        self.init_state_var.set("Init OFF")
        self._update_controls()
        if self.poll_var.get():
            self._start_poll_thread()

    def disconnect(self) -> None:
        self.poll_stop.set()
        self.home_cancel.set()
        self._set_busy(True, "Disconnecting…")
        future = self.executor.submit(self.controller.disconnect)
        future.add_done_callback(
            lambda f: self.ui_queue.put(("disconnect_done", f.exception()))
        )

    def _finish_disconnect(self, error: Optional[BaseException]) -> None:
        self._set_busy(False)
        if error:
            messagebox.showerror(APP_TITLE, str(error), parent=self.root)
            return
        self.connection_var.set("Not connected")
        self.init_state_var.set("Init OFF")
        self.initialized = False
        self.homed = False
        self.bottom_status_var.set("Disconnected")
        self._update_controls()

    def _parse_profile(self) -> Tuple[float, float, float]:
        try:
            speed = float(self.speed_var.get())
            accel = float(self.accel_var.get())
            decel = float(self.decel_var.get())
        except ValueError as exc:
            raise ValueError("Speed, acceleration, and deceleration must be numbers") from exc
        if not 0 < speed <= MAX_SPEED_RPS:
            raise ValueError(f"Speed range: 0–{MAX_SPEED_RPS:g} rps")
        if not 0 < accel <= MAX_ACCEL_RPS2 or not 0 < decel <= MAX_ACCEL_RPS2:
            raise ValueError(f"Acceleration/deceleration range: 0–{MAX_ACCEL_RPS2:g} rps/s")
        return speed, accel, decel

    def init_on(self) -> None:
        try:
            profile = self._parse_profile()
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc), parent=self.root)
            return
        self._submit(
            "Running Init ON…",
            lambda: self.controller.init_and_apply_profile(*profile),
            "Init ON complete; run Home next",
            "init_on",
        )

    def init_off(self) -> None:
        if not self.controller.connected or self.init_off_busy:
            return
        self.init_off_busy = True
        self.home_cancel.set()
        self.poll_pause.set()
        self._update_controls()

        def worker():
            error = None
            try:
                self.controller.init_off()
            except Exception as exc:
                error = exc
            self.ui_queue.put(("init_off_done", error))

        threading.Thread(target=worker, name="motor-init-off", daemon=True).start()

    def _finish_init_off(self, error: Optional[BaseException]) -> None:
        self.init_off_busy = False
        self.poll_pause.clear()
        if error:
            self.bottom_status_var.set(f"Init OFF failed: {error}")
            messagebox.showerror(APP_TITLE, f"Init OFF failed: {error}\nUse the hardware emergency stop.", parent=self.root)
            return
        self.initialized = False
        self.homed = False
        self.init_state_var.set("Init OFF")
        self.command_position_var.set("Not homed")
        self.safety_var.set(False)
        self.bottom_status_var.set("Init OFF sent; confirm the motor output state")
        self._update_controls()

    def apply_profile(self) -> None:
        try:
            profile = self._parse_profile()
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc), parent=self.root)
            return
        self._submit(
            "Applying parameters…",
            lambda: self.controller.apply_profile(*profile),
            "Parameters applied",
        )

    def _confirm_motion(self, description: str) -> bool:
        if not self.safety_var.get():
            messagebox.showwarning(APP_TITLE, "Tick the mechanical safety confirmation first", parent=self.root)
            return False
        return messagebox.askyesno(
            APP_TITLE,
            f"About to execute: {description}\n\nThe status register is not a measured position. Confirm the mechanical path is safe and continue?",
            icon="warning",
            parent=self.root,
        )

    def goto_absolute(self) -> None:
        try:
            position = float(self.absolute_var.get())
        except ValueError:
            messagebox.showwarning(APP_TITLE, "Absolute position must be a number", parent=self.root)
            return
        if abs(position) > MAX_POSITION_UM:
            messagebox.showwarning(APP_TITLE, f"Absolute position software range ±{MAX_POSITION_UM:g} µm", parent=self.root)
            return
        counts = absolute_um_to_counts(position)
        if not self._confirm_motion(f"Absolute position {position:g} µm (raw counts {counts})"):
            return
        self.safety_var.set(False)

        def command():
            self.controller.write_target_counts(counts, f"Absolute {position:g} µm")

        self.pending_target = (counts, f"Absolute {position:g} µm; raw={counts}")
        self._submit("Writing absolute target…", command, "Absolute target written", "target")

    def goto_preset_7250(self) -> None:
        self.absolute_var.set("7250")
        self.goto_absolute()

    def step_move(self, direction: int) -> None:
        try:
            step_um = float(self.step_var.get())
        except ValueError:
            messagebox.showwarning(APP_TITLE, "Step must be a number", parent=self.root)
            return
        if not 0 < step_um <= MAX_STEP_UM:
            messagebox.showwarning(APP_TITLE, f"Step range: 0–{MAX_STEP_UM:g} µm", parent=self.root)
            return
        delta = step_um_to_counts(step_um) * direction
        target = self.command_counts + delta
        label = "Down" if direction > 0 else "Up"
        if not self._confirm_motion(f"{label} {step_um:g} µm (raw increment {delta:+d})"):
            return
        self.safety_var.set(False)

        def command():
            self.controller.write_target_counts(target, f"{label} {step_um:g} µm")

        equivalent_um = target / ABS_SCALE_COUNTS_PER_UM
        self.pending_target = (
            target,
            f"After {label} raw={target}; absolute-scale equivalent {equivalent_um:g} µm",
        )
        self._submit("Writing step target…", command, "Step target written", "target")

    def home(self) -> None:
        try:
            profile = self._parse_profile()
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc), parent=self.root)
            return
        if not self._confirm_motion("Home (search in the homing direction from the capture, up to 180 seconds)"):
            return
        self.safety_var.set(False)
        self.homed = False
        self.command_position_var.set("Home searching…")
        self.home_cancel.clear()
        self.poll_pause.set()

        def report_status(word: int, flag: int):
            self.ui_queue.put(("status_words", word, flag))

        def operation():
            self.controller.home(*profile, self.home_cancel, report_status)

        self._submit("Home searching; click Init OFF at any time…", operation, "Home complete and motion parameters restored", "home")

    def read_status_once(self) -> None:
        def operation():
            word, flag = self.controller.read_status()
            self.ui_queue.put(("status_words", word, flag))

        self._submit("Reading status…", operation, "Status read complete")

    def poll_changed(self) -> None:
        if not self.controller.connected:
            return
        if self.poll_var.get():
            self._start_poll_thread()
        else:
            self.poll_stop.set()

    def _start_poll_thread(self) -> None:
        if self.poll_thread is not None and self.poll_thread.is_alive():
            return
        self.poll_stop.clear()

        def loop():
            failures = 0
            while not self.poll_stop.is_set() and self.controller.connected:
                if self.poll_pause.is_set():
                    self.poll_stop.wait(POLL_INTERVAL_S)
                    continue
                try:
                    word, flag = self.controller.read_status()
                    self.ui_queue.put(("status_words", word, flag))
                    failures = 0
                except Exception as exc:
                    failures += 1
                    if failures == 1:
                        self.ui_queue.put(("log", f"POLL ERROR  {exc}"))
                    if failures >= 3:
                        self.ui_queue.put(("poll_error", f"Continuous status read failed {failures} times: {exc}"))
                self.poll_stop.wait(POLL_INTERVAL_S)

        self.poll_thread = threading.Thread(target=loop, name="motor-status-poll", daemon=True)
        self.poll_thread.start()

    def on_close(self) -> None:
        if self.closing:
            return
        if self.controller.connected and self.initialized:
            if messagebox.askyesno(APP_TITLE, "Send Init OFF before closing?\n\nChoosing \"Yes\" is recommended.", icon="warning", parent=self.root):
                self.home_cancel.set()
                self.poll_pause.set()
                try:
                    self.controller.init_off()
                except Exception as exc:
                    if not messagebox.askyesno(APP_TITLE, f"Init OFF failed: {exc}\nClose anyway?", icon="warning", parent=self.root):
                        return
        self.closing = True
        self.poll_stop.set()
        self.home_cancel.set()
        try:
            self.controller.disconnect()
        finally:
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.root.destroy()


def self_test() -> None:
    assert hex_frame(build_read_input_registers(4, 2)) == "01 04 00 04 00 02 30 0A"
    assert hex_frame(build_write_single(0, 0x0101)) == "01 06 00 00 01 01 49 9A"
    assert hex_frame(build_write_single(0, 0x0301)) == "01 06 00 00 03 01 48 FA"
    assert hex_frame(build_write_single(0, 0x17FF)) == "01 06 00 00 17 FF C6 7A"
    assert hex_frame(build_write_single(0, 0x0200)) == "01 06 00 00 02 00 88 AA"
    assert hex_frame(build_write_single(0, 0x1700)) == "01 06 00 00 17 00 86 3A"
    assert hex_frame(build_write_single(0, 0x0100)) == "01 06 00 00 01 00 88 5A"
    assert step_um_to_counts(2000) == 51200
    assert absolute_um_to_counts(7250) == 742400
    assert (
        hex_frame(build_write_multiple(1, encode_int32_low_word_first(51200)))
        == "01 10 00 01 00 02 04 C8 00 00 00 0C 03"
    )
    assert (
        hex_frame(build_write_multiple(1, encode_int32_low_word_first(742400)))
        == "01 10 00 01 00 02 04 54 00 00 0B 63 94"
    )
    assert (
        hex_frame(build_write_multiple(3, encode_float32_low_word_first(profile_raw(5))))
        == "01 10 00 03 00 02 04 00 00 48 7A 04 59"
    )
    assert (
        hex_frame(build_write_multiple(1, encode_float32_low_word_first(HOME_TARGET_FLOAT)))
        == "01 10 00 01 00 02 04 00 66 C2 F7 C2 9A"
    )


def main() -> None:
    self_test()
    root = tk.Tk()
    MotorApp(root)
    if serial is None:
        root.after(
            100,
            lambda: messagebox.showerror(
                APP_TITLE,
                "pyserial is missing. Run:\npython -m pip install pyserial",
                parent=root,
            ),
        )
    root.mainloop()


if __name__ == "__main__":
    main()
