"""Standalone diagnostic controller for a Thorlabs K10CR1 cage rotator.

Requirements on the Windows control computer:
    1. Install Thorlabs Kinesis (including the K10CR1 device database/driver).
    2. ``py -3.11 -m pip install pythonnet``
    3. Close the Kinesis GUI before running this program.

This program intentionally uses Thorlabs' official Kinesis .NET API rather
than a third-party wrapper.  Motion is never started automatically.  The
operator must type ``arm`` once per connection before a motion command.
"""

from __future__ import annotations

import argparse
import math
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Optional


DEFAULT_SERIAL_NO = "55194494"
DEFAULT_KINESIS_PATHS = (
    Path(r"C:\Program Files\Thorlabs\Kinesis"),
    Path(r"C:\Program Files (x86)\Thorlabs\Kinesis"),
)

POLLING_INTERVAL_MS = 250
SETTINGS_TIMEOUT_MS = 15_000
HOME_TIMEOUT_MS = 120_000
MOVE_TIMEOUT_MS = 120_000
MAX_RELATIVE_MOVE_DEG = 360.0
MAX_UNHOMED_NUDGE_DEG = 1.0

REQUIRED_DLLS = (
    "Thorlabs.MotionControl.DeviceManagerCLI.dll",
    "Thorlabs.MotionControl.GenericMotorCLI.dll",
    "Thorlabs.MotionControl.IntegratedStepperMotorsCLI.dll",
)


class KinesisError(RuntimeError):
    """A Kinesis installation, connection, configuration, or motion error."""


def timestamp() -> str:
    return time.strftime("%H:%M:%S")


def log(message: str) -> None:
    print(f"[{timestamp()}] {message}", flush=True)


def find_kinesis_directory(explicit_path: Optional[str] = None) -> Path:
    """Find a Kinesis folder containing all three required .NET assemblies."""
    candidates = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    environment_path = os.environ.get("THORLABS_KINESIS_PATH", "").strip()
    if environment_path:
        candidates.append(Path(environment_path))
    candidates.extend(DEFAULT_KINESIS_PATHS)

    checked = []
    for candidate in candidates:
        candidate = candidate.expanduser()
        checked.append(str(candidate))
        if all((candidate / name).is_file() for name in REQUIRED_DLLS):
            return candidate

    locations = "\n  - ".join(checked) if checked else "(no candidate directories)"
    raise KinesisError(
        "Complete Thorlabs Kinesis .NET DLLs not found. Checked:\n  - "
        + locations
        + "\nInstall Kinesis, or specify the installation directory with --kinesis-path."
    )


class K10CR1Controller:
    """Strict Kinesis .NET controller for one K10CR1 integrated stage."""

    def __init__(
        self,
        serial_no: str = DEFAULT_SERIAL_NO,
        kinesis_path: Optional[str] = None,
    ) -> None:
        self.serial_no = str(serial_no).strip()
        if not self.serial_no:
            raise ValueError("Device serial number cannot be empty")
        self.kinesis_directory = find_kinesis_directory(kinesis_path)
        self.device = None
        self.motor_configuration = None
        self.polling_started = False
        self.connected = False
        self._load_kinesis()

    def _load_kinesis(self) -> None:
        """Load the exact DLL files used by the official Thorlabs example."""
        try:
            import clr
        except ImportError as error:
            raise KinesisError(
                "pythonnet is not installed. Run: py -3.11 -m pip install pythonnet"
            ) from error

        try:
            for name in REQUIRED_DLLS:
                clr.AddReference(str(self.kinesis_directory / name))

            # DeviceConfiguration belongs to DeviceManagerCLI.  Importing it
            # from a made-up DeviceConfigurationCLI assembly silently skipped
            # the official UseFileSettings path in the previous program.
            from Thorlabs.MotionControl.DeviceManagerCLI import (
                DeviceConfiguration,
                DeviceManagerCLI,
            )
            from Thorlabs.MotionControl.GenericMotorCLI import MotorDirection
            from Thorlabs.MotionControl.IntegratedStepperMotorsCLI import CageRotator
            from System import Convert, Decimal
        except BaseException as error:
            raise KinesisError(
                "Kinesis DLLs found, but loading the .NET classes failed. Confirm that Python and Kinesis "
                "have compatible bitness, and prefer Python 3.11 as verified by the official examples.\n"
                f"Original error: {error}"
            ) from error

        self.DeviceConfiguration = DeviceConfiguration
        self.DeviceManagerCLI = DeviceManagerCLI
        self.MotorDirection = MotorDirection
        self.CageRotator = CageRotator
        self.Convert = Convert
        self.Decimal = Decimal

    def _require_device(self):
        if not self.connected or self.device is None:
            raise KinesisError("K10CR1 is not connected yet")
        return self.device

    def _decimal(self, value: float):
        # This matches Thorlabs' official pythonnet example: Decimal(150.0).
        return self.Decimal(float(value))

    @staticmethod
    def _safe_attribute(obj: Any, name: str, default: Any = None) -> Any:
        try:
            return getattr(obj, name)
        except BaseException:
            return default

    def connect(self) -> None:
        if self.connected:
            return
        log(f"Kinesis directory: {self.kinesis_directory}")
        log("Building device list…")
        try:
            self.DeviceManagerCLI.BuildDeviceList()
            devices = [str(value) for value in self.DeviceManagerCLI.GetDeviceList()]
        except BaseException as error:
            raise KinesisError(f"BuildDeviceList failed: {error}") from error

        log("Detected Kinesis devices: " + (", ".join(devices) or "none"))
        if self.serial_no not in devices:
            raise KinesisError(
                f"Serial number {self.serial_no} not found. Check USB, power, and drivers, close the "
                "Thorlabs Kinesis GUI, and retry."
            )

        device = self.CageRotator.CreateCageRotator(self.serial_no)
        if device is None:
            raise KinesisError(
                f"Serial number {self.serial_no} exists, but a CageRotator object cannot be created; "
                "confirm that it really is a K10CR1/K10CR2."
            )
        self.device = device

        try:
            log(f"Connecting K10CR1 S/N {self.serial_no}…")
            device.Connect(self.serial_no)

            if not device.IsSettingsInitialized():
                log("Waiting for device settings to initialize…")
                device.WaitForSettingsInitialized(SETTINGS_TIMEOUT_MS)
            if not device.IsSettingsInitialized():
                raise KinesisError(
                    f"Device settings still not initialized after waiting {SETTINGS_TIMEOUT_MS / 1000:g} seconds"
                )

            device.StartPolling(POLLING_INTERVAL_MS)
            self.polling_started = True
            time.sleep(0.5)

            device.EnableDevice()
            time.sleep(1.0)

            use_file_settings = (
                self.DeviceConfiguration.DeviceSettingsUseOptionType.UseFileSettings
            )
            self.motor_configuration = device.LoadMotorConfiguration(
                self.serial_no, use_file_settings
            )
            if self.motor_configuration is None:
                raise KinesisError("LoadMotorConfiguration returned an empty configuration")

            settings_name = str(
                self._safe_attribute(
                    self.motor_configuration, "DeviceSettingsName", ""
                )
                or ""
            )
            if settings_name and "K10CR" not in settings_name.upper():
                raise KinesisError(
                    "The loaded motor settings are not K10CR1/K10CR2: "
                    f"DeviceSettingsName={settings_name!r}. Refusing motion to avoid unit or limit errors."
                )

            self.connected = True
            log("Connection, polling, enable, and K10CR configuration loading complete.")
            self.print_configuration()
            self.print_status()
        except BaseException:
            self._cleanup_partial_connection()
            raise

    def _cleanup_partial_connection(self) -> None:
        device = self.device
        if device is None:
            return
        if self.polling_started:
            try:
                device.StopPolling()
            except BaseException:
                pass
        try:
            device.Disconnect()
        except BaseException:
            pass
        self.polling_started = False
        self.connected = False
        self.device = None

    def disconnect(self) -> None:
        device = self.device
        if device is None:
            return
        try:
            status = self.status_snapshot()
            if status.get("IsMoving") or status.get("IsHoming") or status.get("IsJogging"):
                self.stop()
        except BaseException:
            pass
        if self.polling_started:
            try:
                device.StopPolling()
            except BaseException as error:
                log(f"Stop polling warning: {error}")
        try:
            device.Disconnect()
        except BaseException as error:
            log(f"Disconnect warning: {error}")
        finally:
            self.polling_started = False
            self.connected = False
            self.device = None
            self.motor_configuration = None
        log("K10CR1 disconnected.")

    def status_snapshot(self) -> dict[str, Any]:
        device = self._require_device()
        try:
            request_status = getattr(device, "RequestStatus", None)
            if callable(request_status):
                request_status()
                time.sleep(0.1)
        except BaseException:
            pass
        status = device.Status
        names = (
            "IsHomed",
            "IsHoming",
            "IsMoving",
            "IsJogging",
            "IsEnabled",
            "IsDeviceBusy",
            "ForwardLimit",
            "ReverseLimit",
            "IsError",
            "MotorCurrentLimitReached",
        )
        return {
            name: self._safe_attribute(status, name, None)
            for name in names
        }

    def get_position(self) -> float:
        device = self._require_device()
        return float(self.Convert.ToDouble(device.Position))

    def print_status(self) -> None:
        snapshot = self.status_snapshot()
        print()
        try:
            position = f"{self.get_position():.6f} deg"
        except BaseException as error:
            position = f"Read failed: {error}"
        print(f"Position      : {position}")
        for name, value in snapshot.items():
            if value is not None:
                print(f"{name:14}: {value}")
        print()

    def _print_parameter_object(self, title: str, getter_name: str) -> None:
        device = self._require_device()
        getter = getattr(device, getter_name, None)
        if not callable(getter):
            print(f"{title:18}: {getter_name} not in the API")
            return
        try:
            value = getter()
        except BaseException as error:
            print(f"{title:18}: read failed: {error}")
            return
        print(f"{title:18}: {value}")
        property_names = (
            "Direction",
            "Velocity",
            "OffsetDistance",
            "MinimumVelocity",
            "MinVelocity",
            "MaximumVelocity",
            "MaxVelocity",
            "Acceleration",
            "Mode",
            "StepSize",
            "StopMode",
            "ClockwiseHardwareLimit",
            "AnticlockwiseHardwareLimit",
            "ClockwisePosition",
            "AnticlockwisePosition",
            "SoftLimitMode",
        )
        for name in property_names:
            item = self._safe_attribute(value, name, None)
            if item is not None:
                print(f"  {name:27}: {item}")

    def print_configuration(self) -> None:
        device = self._require_device()
        print()
        print("--- Device / configuration diagnostics ---")
        try:
            info = device.GetDeviceInfo()
            for name in (
                "Description",
                "Name",
                "SerialNumber",
                "FirmwareVersion",
                "HardwareVersion",
            ):
                value = self._safe_attribute(info, name, None)
                if value is not None:
                    print(f"DeviceInfo.{name}: {value}")
        except BaseException as error:
            print(f"GetDeviceInfo failed: {error}")

        if self.motor_configuration is not None:
            name = self._safe_attribute(
                self.motor_configuration, "DeviceSettingsName", None
            )
            print(f"DeviceSettingsName: {name}")
        self._print_parameter_object("Velocity parameters", "GetVelocityParams")
        self._print_parameter_object("Homing parameters", "GetHomingParams")
        self._print_parameter_object("Jog parameters", "GetJogParams")
        self._print_parameter_object("Limit parameters", "GetLimitSwitchParams")
        print("------------------------------------------")
        print()

    def identify(self) -> None:
        self._require_device().IdentifyDevice()
        log("Identify sent; watch whether the device indicator blinks.")

    def stop(self) -> None:
        device = self._require_device()
        try:
            device.StopImmediate()
            log("StopImmediate sent.")
        except BaseException as first_error:
            try:
                device.Stop(5_000)
                log("Stop sent.")
            except BaseException as second_error:
                raise KinesisError(
                    f"StopImmediate failed: {first_error}; Stop also failed: {second_error}"
                ) from second_error

    def recover_enable(self) -> None:
        device = self._require_device()
        try:
            self.stop()
        except BaseException:
            pass
        device.EnableDevice()
        time.sleep(1.0)
        log("EnableDevice re-sent.")
        self.print_status()

    def _motion_failure(self, action: str, error: BaseException) -> KinesisError:
        try:
            self.stop()
        except BaseException:
            pass
        try:
            self.print_status()
        except BaseException:
            pass
        return KinesisError(f"{action} failed, immediate stop attempted: {error}")

    def home(self) -> None:
        device = self._require_device()
        status = self.status_snapshot()
        if status.get("ForwardLimit") and status.get("ReverseLimit"):
            raise KinesisError("Forward and reverse limits triggered simultaneously; refusing Home, check the sensors/configuration first")
        log(f"Starting Home, timeout {HOME_TIMEOUT_MS / 1000:g} s…")
        try:
            device.Home(HOME_TIMEOUT_MS)
        except BaseException as error:
            raise self._motion_failure("Home", error) from error
        time.sleep(0.25)
        if not bool(self.status_snapshot().get("IsHomed")):
            raise self._motion_failure(
                "Home", KinesisError("IsHomed is still False after the command returned")
            )
        log(f"Home complete; position {self.get_position():.6f} deg.")

    def _move_relative_impl(self, delta_deg: float, require_homed: bool) -> None:
        delta_deg = float(delta_deg)
        if not math.isfinite(delta_deg):
            raise ValueError("Relative angle must be a finite number")
        if abs(delta_deg) < 1e-12:
            log("Relative angle is 0; no motion command sent.")
            return
        if abs(delta_deg) > MAX_RELATIVE_MOVE_DEG:
            raise ValueError(
                f"A single relative move is limited to ±{MAX_RELATIVE_MOVE_DEG:g} deg"
            )
        if require_homed and not bool(self.status_snapshot().get("IsHomed")):
            raise KinesisError("Home has not succeeded yet; only ≤1° diagnostic nudges with the n command are allowed")

        direction = (
            self.MotorDirection.Forward
            if delta_deg > 0
            else self.MotorDirection.Backward
        )
        distance = abs(delta_deg)
        log(f"Relative move {delta_deg:+.6f} deg…")
        try:
            self._require_device().MoveRelative(
                direction, self._decimal(distance), MOVE_TIMEOUT_MS
            )
        except BaseException as error:
            raise self._motion_failure("MoveRelative", error) from error
        log(f"Relative move complete; position {self.get_position():.6f} deg.")

    def diagnostic_nudge(self, delta_deg: float) -> None:
        delta_deg = float(delta_deg)
        if not math.isfinite(delta_deg) or abs(delta_deg) > MAX_UNHOMED_NUDGE_DEG:
            raise ValueError(
                f"Unhomed diagnostic nudge must be within ±{MAX_UNHOMED_NUDGE_DEG:g} deg"
            )
        self._move_relative_impl(delta_deg, require_homed=False)

    def move_relative(self, delta_deg: float) -> None:
        self._move_relative_impl(delta_deg, require_homed=True)

    def move_to(self, angle_deg: float) -> None:
        angle_deg = float(angle_deg)
        if not math.isfinite(angle_deg) or not 0.0 <= angle_deg <= 360.0:
            raise ValueError("Absolute angle must be within 0–360 deg")
        if not bool(self.status_snapshot().get("IsHomed")):
            raise KinesisError("Home must succeed before an absolute move")
        log(f"Absolute move to {angle_deg:.6f} deg…")
        try:
            self._require_device().MoveTo(
                self._decimal(angle_deg), MOVE_TIMEOUT_MS
            )
        except BaseException as error:
            raise self._motion_failure("MoveTo", error) from error
        log(f"Absolute move complete; position {self.get_position():.6f} deg.")

    def jog(self, forward: bool) -> None:
        if not bool(self.status_snapshot().get("IsHomed")):
            raise KinesisError("Home must succeed before Jog")
        direction = (
            self.MotorDirection.Forward
            if forward
            else self.MotorDirection.Backward
        )
        label = "Forward" if forward else "Backward"
        log(f"MoveJog {label} (step from the Kinesis configuration)…")
        try:
            self._require_device().MoveJog(direction, MOVE_TIMEOUT_MS)
        except BaseException as error:
            raise self._motion_failure("MoveJog", error) from error
        log(f"Jog complete; position {self.get_position():.6f} deg.")

    def monitor_position(self, seconds: float = 5.0) -> None:
        seconds = min(max(float(seconds), 0.5), 3600.0)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            status = self.status_snapshot()
            log(
                f"pos={self.get_position():.6f} deg | "
                f"moving={status.get('IsMoving')} | "
                f"homing={status.get('IsHoming')} | "
                f"homed={status.get('IsHomed')}"
            )
            time.sleep(0.5)


def print_help() -> None:
    print(
        """
Commands
  id             identify by device indicator (no motion)
  p              current status and position (no motion)
  diag           device, velocity, Home, Jog, limit configuration (no motion)
  mon [seconds]  monitor position continuously, default 5 seconds (no motion)
  arm            allow motion for this connection; requires typing ARM to confirm
  disarm         forbid further motion
  h              Home
  n 0.2          diagnostic nudge before a successful Home, range only ±1°
  r 1            relative move +1° after Home
  r -1           relative move -1° after Home
  m 90           absolute move to 90° after Home
  j+ / j-        move by the Jog Step saved in Kinesis
  enable         re-send EnableDevice after Stop
  s              stop immediately
  help           show this help
  q              stop (if moving) and disconnect
"""
    )


def require_armed(armed: bool) -> None:
    if not armed:
        raise PermissionError("Motion not authorized. Enter arm first and type ARM when prompted.")


def run_console(controller: K10CR1Controller) -> None:
    armed = False
    print_help()
    while True:
        try:
            command = input("K10CR1> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not command:
            continue
        parts = command.split()
        key = parts[0].lower()
        try:
            if key in {"q", "quit", "exit"}:
                break
            if key in {"help", "?"}:
                print_help()
            elif key == "id":
                controller.identify()
            elif key in {"p", "status"}:
                controller.print_status()
            elif key == "diag":
                controller.print_configuration()
                controller.print_status()
            elif key == "mon":
                controller.monitor_position(float(parts[1]) if len(parts) == 2 else 5.0)
            elif key == "arm":
                confirmation = input(
                    "After confirming the mechanical path, rotation direction, cables, and sample are safe, type ARM: "
                ).strip()
                armed = confirmation == "ARM"
                print("Motion authorized." if armed else "Motion not authorized.")
            elif key == "disarm":
                armed = False
                print("Motion disabled.")
            elif key in {"s", "stop"}:
                controller.stop()
            elif key == "enable":
                controller.recover_enable()
            elif key in {"h", "home"}:
                require_armed(armed)
                controller.home()
            elif key == "n" and len(parts) == 2:
                require_armed(armed)
                controller.diagnostic_nudge(float(parts[1]))
            elif key == "r" and len(parts) == 2:
                require_armed(armed)
                controller.move_relative(float(parts[1]))
            elif key == "m" and len(parts) == 2:
                require_armed(armed)
                controller.move_to(float(parts[1]))
            elif key == "j+":
                require_armed(armed)
                controller.jog(True)
            elif key == "j-":
                require_armed(armed)
                controller.jog(False)
            else:
                print("Incorrect command or argument; type help for usage.")
        except BaseException as error:
            print(f"ERROR: {error}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Thorlabs K10CR1 Kinesis diagnostic controller"
    )
    parser.add_argument(
        "--serial",
        default=DEFAULT_SERIAL_NO,
        help=f"Kinesis serial number (default: {DEFAULT_SERIAL_NO})",
    )
    parser.add_argument(
        "--kinesis-path",
        help=r"Kinesis folder, e.g. C:\Program Files\Thorlabs\Kinesis",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if platform.system() != "Windows":
        print("ERROR: the Thorlabs Kinesis .NET DLL is only supported on Windows.", file=sys.stderr)
        return 2

    controller = None
    try:
        controller = K10CR1Controller(args.serial, args.kinesis_path)
        controller.connect()
        run_console(controller)
        return 0
    except BaseException as error:
        print(f"FATAL: {error}", file=sys.stderr)
        return 1
    finally:
        if controller is not None:
            try:
                controller.disconnect()
            except BaseException as error:
                print(f"Close warning: {error}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
