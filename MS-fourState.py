"""MS-fourState: write the magnetic states D, A, B and E in the PPMS and read
each one at zero field with the SR830 first harmonic.

One step, with the state's angle, write field and pulse:

  1. 6221 off (read back); the field is brought to 0 Oe if it is not there.
  2. The PPMS rotator turns to the state's angle, only at zero field.
  3. The write field is set (Driven) and allowed to settle.
  4. The 6221 sends the write pulse(s): 0 -> I for the pulse width.
  5. The field returns to 0 Oe (Driven).
  6. Read: the 6221 sources a sine of I_read (RMS) whose phase marker is the
     SR830's external reference, as on the PPMS page; the SR830 first
     harmonic X, Y and theta are averaged over the read time.
  7. 6221 off; the point is plotted and written to the CSV.

The Mode list runs it two ways: Cycle repeats a cycle of states (default
D-A-B-E-B-A, so D-A-B-E-B-A-D-A-...) a set number of times or until Stop;
Sequence writes and reads the states once, in the order typed (for example
A B A B A B to switch between two states).

Stop, an error or the end of the run turns the 6221 off and returns the field
to 0 Oe (Driven), confirmed by readback.  The rotator stays where it is: it
has no software stop, so keep MultiVu's Motion Control at hand.

Manual settings, as on the main program's PPMS page, set one thing at a time
between runs: Move turns the rotator (only at zero field, 6221 off first);
Set H and Zero H go to a field and end Persistent; Set I starts the read sine
and leaves it on; I off turns the 6221 off; Pulse sends one write pulse train
with the Write settings; Read shows the angle, field, temperature and 6221
output.  Stop during one of them does what it does in a run (6221 off, 0 Oe
Driven); an error turns the 6221 off and leaves the field and angle.

This file runs on its own: the instrument code it shares with
Transport_all_NH_dev.py (the 6221, SR830 and PPMS controllers and the
rotator) is copied into it, between the BEGIN and END lines below.  Close the
main program's PPMS, 6221 and SR830 connections first: two programs must
never drive the same instruments.

    python MS-fourState.py
"""
import csv
import json
import math
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
import types
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

# ===== BEGIN copied from Transport_all_NH_dev.py (Other-files/tools/ms_fourstate_copy_core.py) =====
# The instrument code MS-fourState shares with the main program, copied so
# this file runs on its own.  Do not edit here: fix the main program and run
# Other-files/tools/ms_fourstate_copy_core.py.
# Copied from Transport_all_NH_dev.py, SHA-256 d09734a93ba3…
import importlib


import math


import os


import re


import socket


import sys


import threading


import time


from pathlib import Path


from typing import Callable


try:
    import pyvisa
except ImportError:
    pyvisa = None


def short_status_error(error: BaseException, limit: int = 120) -> str:
    """One-line error summary suitable for a fixed-height Tk status bar."""
    text = " ".join(str(error).replace("\r", "\n").split())
    first_sentence = re.split(r"(?<=[。；;])\s+|\n", text, maxsplit=1)[0]
    if len(first_sentence) <= limit:
        return first_sentence
    return first_sentence[: max(1, limit - 1)].rstrip() + "…"


K6221_DEFAULT_RESOURCE = "GPIB0::30::INSTR"


K6221_MAX_ABS_CURRENT_A = 0.105


K6221_MIN_NONZERO_CURRENT_A = 4e-12


K6221_MIN_PULSE_WIDTH_S = 5e-6


K6221_MAX_PULSE_WIDTH_S = 500.0


K6221_MIN_COMPLIANCE_V = 0.1


K6221_MAX_COMPLIANCE_V = 105.0


K6221_OUTPUT_LOW_FLOATING = "OUTP:LTE OFF"


K6221_OUTPUT_LOW_QUERY = "OUTP:LTE?"


K6221_DEFAULT_PULSE_WIDTH_US = 10.0


K6221_DEFAULT_PULSE_COUNT = 1


K6221_MAX_PULSE_COUNT = 99_999_999_900


K6221_MAX_WAVEFORM_TIME_S = 999_999.999


K6221_DEFAULT_COMPLIANCE_V = 50.0


NONLOCAL_MARKER_LINE = 4


NONLOCAL_MARKER_PHASE_DEG = 0.0


NONLOCAL_SR830_FULL_SCALE_V = (
    2e-9, 5e-9, 10e-9, 20e-9, 50e-9, 100e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6, 200e-6,
    500e-6, 1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3,
    200e-3, 500e-3, 1.0,
)


PPMS_DEFAULT_HOST = "192.168.137.5"


PPMS_HOST_CHOICES = ("192.168.137.5", "192.168.137.163")


PPMS_QDINSTRUMENT_BACKEND = "QDInstrument remote"


PPMS_MULTIPYVU_BACKEND = "MultiPyVu"


PPMS_QDINSTRUMENT_DEFAULT_PORT = 11000


PPMS_QDINSTRUMENT_DLL_ENV = "MOKE_QDINSTRUMENT_DLL"


PPMS_TEMPERATURE_MIN_K = 5.0


PPMS_TEMPERATURE_MAX_K = 380.0


PPMS_TEMPERATURE_LOW_BOUNDARY_K = 9.0


PPMS_TEMPERATURE_MAX_RATE_K_MIN = 10.0


PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN = 1.0


PPMS_FIELD_LIMIT_OE = 89_900.0


PPMS_FIELD_RATE_LIMIT_OE_S = 150.0  # user requirement is strictly below 150


PPMS_FIELD_RATE_DEFAULT_OE_S = 100.0


PPMS_FIELD_TOLERANCE_OE = 2.0


PPMS_FIELD_RELATIVE_TOLERANCE = 0.0


PPMS_PERSISTENT_CONFIRM_S = 10.0


PPMS_PERSISTENT_MARGIN_S = 1800.0


PPMS_TRANSPORT_FREQUENCY_HZ = 317.3


PPMS_TRANSPORT_REFERENCE_TIMEOUT_S = 8.0


PPMS_SR830_RESOURCE_OPTIONS = tuple(
    f"GPIB0::{address}::INSTR" for address in (8, 9, 10)
)


PPMS_SR830_REFERENCE_SLOPE = 1


_INSTRUMENT_LEASE_LOCK = threading.RLock()


_INSTRUMENT_LEASES: dict[str, tuple[object, str]] = {}


_SHARED_VISA_MANAGER_LOCK = threading.RLock()


_SHARED_VISA_MANAGER = None


_SHARED_VISA_MANAGER_USERS = 0


def acquire_shared_visa_manager():
    global _SHARED_VISA_MANAGER, _SHARED_VISA_MANAGER_USERS
    if pyvisa is None:
        raise RuntimeError(
            "pyvisa is missing; please run: py -m pip install pyvisa pyvisa-py"
        )
    with _SHARED_VISA_MANAGER_LOCK:
        manager = _SHARED_VISA_MANAGER
        if manager is not None:
            try:
                manager.session
            except BaseException as error:
                if _SHARED_VISA_MANAGER_USERS:
                    raise RuntimeError(
                        "The shared VISA resource manager session became invalid unexpectedly; please close the instruments "
                        "that still show as connected and reconnect"
                    ) from error
                _SHARED_VISA_MANAGER = None
                manager = None
        if manager is None:
            manager = pyvisa.ResourceManager()
            # Force validation here so a stale manager is rejected before a
            # controller stores it as an active connection.
            manager.session
            _SHARED_VISA_MANAGER = manager
        _SHARED_VISA_MANAGER_USERS += 1
        return manager


def release_shared_visa_manager(manager) -> None:
    global _SHARED_VISA_MANAGER, _SHARED_VISA_MANAGER_USERS
    if manager is None:
        return
    with _SHARED_VISA_MANAGER_LOCK:
        if manager is not _SHARED_VISA_MANAGER:
            return
        _SHARED_VISA_MANAGER_USERS = max(
            0, _SHARED_VISA_MANAGER_USERS - 1
        )
        if _SHARED_VISA_MANAGER_USERS == 0:
            # Close while holding the pool lock.  PyVISA keeps this manager in
            # its own backend registry until close() returns; allowing another
            # acquire in that small window could hand out the manager being
            # closed.
            try:
                manager.close()
            except BaseException:
                pass
            finally:
                _SHARED_VISA_MANAGER = None


def instrument_lease_key(resource: str) -> str:
    """Normalize only unambiguous GPIB spellings for cross-module ownership.

    Keep secondary addresses and board numbers distinct. The original resource
    still goes to VISA unchanged; this does not resolve vendor-defined aliases.
    """
    key = str(resource).strip().upper()
    # ::INSTR is optional in VISA: GPIB0::30 is the same 6221 (review, 2026-10-09).
    match = re.fullmatch(r"GPIB(\d*)::(\d+)(?:::(\d+))?(?:::INSTR)?", key)
    if match:
        board, primary, secondary = match.groups()
        key = f"GPIB{int(board or '0')}::{int(primary)}"
        if secondary is not None:
            key += f"::{int(secondary)}"
        key += "::INSTR"
    return key


def acquire_instrument_lease(
    resource: str, owner: object, owner_label: str
) -> str:
    key = instrument_lease_key(resource)
    if not key:
        raise ValueError("The instrument resource address cannot be empty")
    with _INSTRUMENT_LEASE_LOCK:
        current = _INSTRUMENT_LEASES.get(key)
        if current is not None:
            raise RuntimeError(
                f"{resource} is already connected by \"{current[1]}\"; "
                f"please disconnect it in that module first, then connect from \"{owner_label}\""
            )
        _INSTRUMENT_LEASES[key] = (owner, str(owner_label))
    return key


def release_instrument_lease(resource: str, owner: object) -> None:
    key = instrument_lease_key(resource)
    if not key:
        return
    with _INSTRUMENT_LEASE_LOCK:
        current = _INSTRUMENT_LEASES.get(key)
        if current is not None and current[0] is owner:
            _INSTRUMENT_LEASES.pop(key, None)


class SafeVisaIdentityController:
    """One leased VISA session with fail-closed source disconnect semantics."""

    def __init__(
        self,
        logger: Callable[[str], None],
        label: str,
        identity_token: str,
        safe_off_commands: tuple[str, ...] = (),
        owner_scope: str = "PPMS",
        preserve_visa_io_defaults: bool = False,
        safe_off_on_connect: bool = True,
        enumerate_before_open: bool = False,
        connect_settle_seconds: float = 0.0,
    ) -> None:
        self.logger = logger
        self.label = str(label)
        self.identity_token = str(identity_token).upper()
        self.safe_off_commands = tuple(safe_off_commands)
        self.owner_scope = str(owner_scope)
        self.preserve_visa_io_defaults = bool(preserve_visa_io_defaults)
        self.safe_off_on_connect = bool(safe_off_on_connect)
        self.enumerate_before_open = bool(enumerate_before_open)
        self.connect_settle_seconds = max(
            0.0, float(connect_settle_seconds)
        )
        self.resource_manager = None
        self.instrument = None
        self.resource_name = ""
        self.identity = ""
        self.lock = threading.RLock()

    @property
    def connected(self) -> bool:
        return self.instrument is not None

    def connect(self, resource_name: str) -> str:
        if pyvisa is None:
            raise RuntimeError(
                "pyvisa is missing; please run: py -m pip install pyvisa pyvisa-py"
            )
        resource_name = str(resource_name).strip()
        with self.lock:
            if self.instrument is not None:
                return self.identity
            acquire_instrument_lease(
                resource_name, self, f"{self.owner_scope} / {self.label}"
            )
            manager = None
            instrument = None
            identity_verified = False
            try:
                manager = acquire_shared_visa_manager()
                if self.enumerate_before_open:
                    # The legacy BIT 4886 path was only reliable on the
                    # experiment PC after NI-VISA had enumerated the GPIB bus.
                    # This is read-only discovery and sends no programming
                    # command to the power supply.
                    manager.list_resources()
                instrument = manager.open_resource(resource_name)
                instrument.timeout = (
                    3000 if self.preserve_visa_io_defaults else 5000
                )
                # Most newer SCPI instruments in this application explicitly
                # use LF.  Older GPIB instruments such as the KEPCO BIT 4886
                # instead rely on the native IEEE-488 EOI handshake.  In that
                # case preserve the backend defaults (including send_end=True)
                # exactly as a plain PyVISA open_resource/query sequence does.
                if not self.preserve_visa_io_defaults:
                    instrument.write_termination = "\n"
                    instrument.read_termination = "\n"
                    try:
                        instrument.query_delay = 0.05
                    except (AttributeError, ValueError):
                        pass
                else:
                    # Match the I/O profile verified by the read-only
                    # Scan_GPIB.py diagnostic on the STFMR computer.  The BIT
                    # 4886 accepts CR/LF plus the IEEE-488 EOI handshake and
                    # returns its reply by EOI rather than a text terminator.
                    instrument.write_termination = "\r\n"
                    instrument.read_termination = None
                    instrument.send_end = True
                if self.connect_settle_seconds > 0:
                    # Scan_GPIB.py reaches GPIB address 6 only after its first
                    # serial-port timeout.  Reproduce that known-good warm-up
                    # interval so the first *IDN? is not issued immediately
                    # after opening the old BIT 4886 GPIB session.
                    time.sleep(self.connect_settle_seconds)
                try:
                    identity = instrument.query("*IDN?").strip()
                except BaseException as error:
                    raise RuntimeError(
                        f"{self.label} identity query *IDN? failed: {error}; "
                        "no output/setting command was sent to the device this time; the VISA session has been closed"
                    ) from error
                if self.identity_token not in identity.upper():
                    raise RuntimeError(
                        f"The device returned by {resource_name} is not {self.label}: {identity}"
                    )
                identity_verified = True
                # Connecting a source must never inherit an output left on by
                # an earlier crashed program.  SR830 rows have no off commands.
                if self.safe_off_on_connect:
                    for command in self.safe_off_commands:
                        try:
                            instrument.write(command)
                        except BaseException as error:
                            raise RuntimeError(
                                f"{self.label} connection safety command {command!r} failed: {error}"
                            ) from error
                self.resource_manager = manager
                self.instrument = instrument
                self.resource_name = resource_name
                self.identity = identity
                self.logger(
                    f"{self.owner_scope} {self.label} connected: {identity}"
                )
                return identity
            except BaseException:
                if instrument is not None:
                    # Never send source-control commands to an instrument whose
                    # identity query failed.  Besides being unsafe for a wrong
                    # device, doing so after a timed-out GPIB query can leave an
                    # old controller in a bus state that also breaks the next
                    # standalone Scan_GPIB.py run.
                    if identity_verified and self.safe_off_on_connect:
                        for command in self.safe_off_commands:
                            try:
                                instrument.write(command)
                            except BaseException:
                                pass
                    try:
                        instrument.close()
                    except BaseException:
                        pass
                release_shared_visa_manager(manager)
                release_instrument_lease(resource_name, self)
                raise

    def safe_output_off(self) -> None:
        with self.lock:
            if self.instrument is None:
                return
            errors = []
            for command in self.safe_off_commands:
                try:
                    self.instrument.write(command)
                except BaseException as error:
                    errors.append(f"{command}: {error}")
            if errors:
                raise RuntimeError(
                    f"{self.label} failed to turn output off: " + "; ".join(errors)
                )

    def disconnect(self) -> None:
        with self.lock:
            instrument = self.instrument
            manager = self.resource_manager
            resource_name = self.resource_name
            if instrument is None and manager is None:
                return
            shutdown_error = None
            try:
                self.safe_output_off()
            except BaseException as error:
                shutdown_error = error
            if instrument is not None:
                try:
                    instrument.write("SYST:LOC")
                except BaseException:
                    pass
                try:
                    instrument.close()
                except BaseException:
                    pass
            release_shared_visa_manager(manager)
            self.instrument = None
            self.resource_manager = None
            self.resource_name = ""
            self.identity = ""
            release_instrument_lease(resource_name, self)
            self.logger(f"{self.owner_scope} {self.label} disconnected")
            if shutdown_error is not None:
                raise shutdown_error


class PPMSMultiVuReadbackController:
    """PPMS sample-environment client using official Quantum Design MultiPyVu."""

    def __init__(self, logger: Callable[[str], None]) -> None:
        self.logger = logger
        self.client = None
        self.host = ""
        self.port = 0
        self.lock = threading.RLock()

    @property
    def connected(self) -> bool:
        return self.client is not None

    @staticmethod
    def validate_endpoint(host: str, port: int) -> tuple[str, int]:
        host = str(host).strip()
        if not host:
            raise ValueError("PPMS IP address cannot be empty")
        try:
            socket.getaddrinfo(host, int(port), type=socket.SOCK_STREAM)
        except (OSError, ValueError) as error:
            raise ValueError(f"PPMS IP address or port is invalid: {host}:{port}") from error
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError("PPMS TCP port must be between 1 and 65535")
        return host, port

    def connect(self, host: str, port: int) -> dict[str, object]:
        host, port = self.validate_endpoint(host, port)
        with self.lock:
            if self.client is not None:
                return self.read_environment()
            try:
                module = importlib.import_module("MultiPyVu")
            except ImportError as error:
                raise RuntimeError(
                    "Quantum Design MultiPyVu is missing; please run on this machine:\n"
                    "py -m pip install --upgrade MultiPyVu\n\n"
                    "and start MultiVu and the MultiPyVu Server on the PPMS host."
                ) from error
            client = module.Client(host=host, port=port)
            try:
                client.open()
                self.client = client
                self.host = host
                self.port = port
                result = self.read_environment()
            except BaseException:
                try:
                    client.close_client()
                except BaseException:
                    pass
                self.client = None
                raise
            self.logger(f"PPMS MultiVu connected: {host}:{port}")
            return result

    def read_environment(self) -> dict[str, object]:
        with self.lock:
            if self.client is None:
                raise RuntimeError("PPMS MultiVu is not connected")
            temperature_k, temperature_status = self.client.get_temperature()
            field_oe, field_status = self.client.get_field()
        temperature_k = float(temperature_k)
        field_oe = float(field_oe)
        if not math.isfinite(temperature_k) or not math.isfinite(field_oe):
            raise RuntimeError(
                f"PPMS returned non-finite readings: T={temperature_k!r}, H={field_oe!r}"
            )
        return {
            "temperature_k": temperature_k,
            "temperature_status": getattr(
                temperature_status, "name", str(temperature_status)
            ),
            "field_oe": field_oe,
            "field_status": getattr(field_status, "name", str(field_status)),
        }

    @staticmethod
    def _enum_member(client, modern_group: str, legacy_group: str, name: str):
        group = getattr(client, modern_group, None)
        if group is not None:
            nested = getattr(group, "approach_mode", None)
            if nested is not None and hasattr(nested, name):
                return getattr(nested, name)
            nested = getattr(group, "driven_mode", None)
            if nested is not None and hasattr(nested, name):
                return getattr(nested, name)
        group = getattr(client, legacy_group, None)
        if group is not None and hasattr(group, name):
            return getattr(group, name)
        raise RuntimeError(f"The current MultiPyVu version lacks the mode enum {name}")

    def set_temperature(
        self, target_k: float, rate_k_min: float, approach_code: int
    ) -> dict[str, object]:
        with self.lock:
            if self.client is None:
                raise RuntimeError("PPMS MultiVu is not connected")
            current_k, _status = self.client.get_temperature()
            target_k, rate_k_min = validate_ppms_temperature_command(
                target_k, rate_k_min, float(current_k)
            )
            approach_name = "fast_settle" if int(approach_code) == 0 else "no_overshoot"
            approach = self._enum_member(
                self.client, "temperature", "temp_approach_mode", approach_name
            )
            self.client.set_temperature(target_k, float(rate_k_min), approach)
            return self.read_environment()

    def set_field(
        self,
        target_oe: float,
        rate_oe_s: float,
        approach_code: int,
        mode_code: int,
    ) -> dict[str, object]:
        target_oe, rate_oe_s = validate_ppms_field_command(target_oe, rate_oe_s)
        approach_names = {0: "linear", 1: "no_overshoot", 2: "oscillate"}
        mode_names = {0: "persistent", 1: "driven"}
        try:
            approach_name = approach_names[int(approach_code)]
            mode_name = mode_names[int(mode_code)]
        except KeyError as error:
            raise ValueError("PPMS field approach/mode parameters are invalid") from error
        with self.lock:
            if self.client is None:
                raise RuntimeError("PPMS MultiVu is not connected")
            approach = self._enum_member(
                self.client, "field", "field_approach_mode", approach_name
            )
            mode = self._enum_member(
                self.client, "field", "field_driven_mode", mode_name
            )
            self.client.set_field(target_oe, rate_oe_s, approach, mode)
            return self.read_environment()

    def disconnect(self) -> None:
        with self.lock:
            client = self.client
            self.client = None
            self.host = ""
            self.port = 0
            if client is None:
                return
            # Close only this client.  Never call close_server(), and never
            # alter an in-progress PPMS temperature or field ramp on exit.
            client.close_client()
            self.logger("PPMS MultiVu client disconnected (current setpoints unchanged)")


class QDCommandUncertain(RuntimeError):
    """A command was invoked once, but its acknowledgement is ambiguous."""


class PPMSQDInstrumentReadbackController:
    """Remote PPMS client using QDInstrument.dll over TCP 11000."""

    def __init__(self, logger: Callable[[str], None]) -> None:
        self.logger = logger
        self.instrument = None
        self.host = ""
        self.port = 0
        self.dll_path = ""
        self.lock = threading.RLock()

    @property
    def connected(self) -> bool:
        return self.instrument is not None

    @staticmethod
    def candidate_dll_paths(explicit_path: str = "") -> tuple[Path, ...]:
        candidates: list[Path] = []
        for raw_path in (
            explicit_path,
            os.environ.get(PPMS_QDINSTRUMENT_DLL_ENV, ""),
        ):
            if str(raw_path).strip():
                candidates.append(Path(str(raw_path).strip().strip('"')))
        script_directory = Path(__file__).resolve().parent
        executable_directory = Path(sys.executable).resolve().parent
        working_directory = Path.cwd()
        candidates.extend(
            (
                script_directory / "QDInstrument.dll",
                script_directory / "QDInstrument" / "QDInstrument.dll",
                executable_directory / "QDInstrument.dll",
                executable_directory / "QDInstrument" / "QDInstrument.dll",
                working_directory / "QDInstrument.dll",
                working_directory / "QDInstrument" / "QDInstrument.dll",
                Path(r"C:\QDLabVIEW\QDInstrument.dll"),
                Path(r"C:\QDInstrument\QDInstrument.dll"),
                Path(r"C:\ProgramData\Quantum Design\QDInstrument\QDInstrument.dll"),
                Path(r"C:\Program Files\Quantum Design\QDInstrument\QDInstrument.dll"),
                Path(r"C:\Program Files (x86)\Quantum Design\QDInstrument\QDInstrument.dll"),
                Path(r"C:\Program Files\National Instruments\LabVIEW 2020\instr.lib\QDInstrument_LabView\QDInstrument.dll"),
                Path(r"C:\Program Files (x86)\National Instruments\LabVIEW 2020\instr.lib\QDInstrument_LabView\QDInstrument.dll"),
                Path(r"C:\Program Files\National Instruments\LabVIEW 2025\QDInstrument.dll"),
                Path(r"C:\Program Files (x86)\National Instruments\LabVIEW 2025\QDInstrument.dll"),
                Path(r"D:\user data\Niu Heng\QDInstrument.dll"),
                Path(r"D:\user data\Niu Heng\QDInstrument\QDInstrument.dll"),
            )
        )
        # A LabVIEW project often keeps vendor .NET assemblies beside its VIs,
        # while a frozen/Nuitka build may put the assembly beside the exe.
        # Include those runtime search locations without recursively walking
        # the whole system during normal startup.
        for raw_directory in (
            *sys.path,
            *os.environ.get("PATH", "").split(os.pathsep),
        ):
            if str(raw_directory).strip():
                candidates.append(
                    Path(str(raw_directory).strip().strip('"'))
                    / "QDInstrument.dll"
                )
        unique: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path).casefold()
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return tuple(unique)

    @staticmethod
    def _windows_discovery_roots() -> tuple[Path, ...]:
        """Return bounded roots likely to contain the LabVIEW QD assembly."""
        roots: list[Path] = []
        for variable in (
            "USERPROFILE",
            "PUBLIC",
            "PROGRAMDATA",
            "ProgramFiles",
            "ProgramFiles(x86)",
        ):
            raw = os.environ.get(variable, "").strip()
            if not raw:
                continue
            base = Path(raw)
            if variable in ("USERPROFILE", "PUBLIC"):
                roots.extend((base / "Desktop", base / "Documents"))
            elif variable == "PROGRAMDATA":
                roots.append(base / "Quantum Design")
            else:
                roots.extend(
                    (
                        base / "Quantum Design",
                        base / "National Instruments",
                    )
                )
        roots.extend(
            (
                Path(r"C:\QDInstrument"),
                Path(r"C:\QDLabVIEW"),
                Path(r"D:\user data\Niu Heng"),
            )
        )
        unique: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            key = str(root).casefold()
            if key not in seen:
                seen.add(key)
                unique.append(root)
        return tuple(unique)

    @classmethod
    def discover_dll_paths(
        cls,
        explicit_path: str = "",
        *,
        recursive: bool = False,
        time_limit_s: float = 8.0,
        max_results: int = 8,
    ) -> tuple[Path, ...]:
        """Find QDInstrument.dll without making normal startup scan drives.

        Direct candidates are cheap and always checked.  The optional bounded
        recursive pass is used only by the GUI's explicit auto-find action or
        after normal .NET assembly resolution failed.
        """
        matches: list[Path] = []
        seen: set[str] = set()

        def add(path: Path) -> bool:
            try:
                if not path.is_file():
                    return False
                resolved = path.resolve()
            except (OSError, RuntimeError):
                return False
            key = str(resolved).casefold()
            if key in seen:
                return False
            seen.add(key)
            matches.append(resolved)
            return len(matches) >= max(1, int(max_results))

        for path in cls.candidate_dll_paths(explicit_path):
            if add(path):
                return tuple(matches)
        if not recursive or not sys.platform.startswith("win"):
            return tuple(matches)

        deadline = time.monotonic() + max(0.5, float(time_limit_s))
        skipped_names = {
            "$recycle.bin",
            ".git",
            ".svn",
            "appdata",
            "cache",
            "node_modules",
            "site-packages",
            "temp",
            "windowsapps",
        }
        for root in cls._windows_discovery_roots():
            if time.monotonic() >= deadline:
                break
            try:
                if not root.is_dir():
                    continue
                root_depth = len(root.parts)
                for current, directory_names, file_names in os.walk(root):
                    if time.monotonic() >= deadline:
                        break
                    current_path = Path(current)
                    depth = len(current_path.parts) - root_depth
                    directory_names[:] = [
                        name
                        for name in directory_names
                        if name.casefold() not in skipped_names
                        and not name.startswith(".")
                    ]
                    if depth >= 7:
                        directory_names[:] = []
                    for file_name in file_names:
                        if file_name.casefold() == "qdinstrument.dll":
                            if add(current_path / file_name):
                                return tuple(matches)
            except (OSError, PermissionError):
                continue
        return tuple(matches)

    @classmethod
    def locate_dll(cls, explicit_path: str = "") -> Path:
        candidates = cls.candidate_dll_paths(explicit_path)
        matches = cls.discover_dll_paths(explicit_path)
        if matches:
            return matches[0]
        checked = "\n".join(f"  - {path}" for path in candidates)
        raise RuntimeError(
            "QDInstrument.dll was not found. Please select that DLL from the currently available LabVIEW QDInstrument "
            "directory, or set the environment variable "
            f"{PPMS_QDINSTRUMENT_DLL_ENV}. Checked:\n{checked}"
        )

    @staticmethod
    def _import_qdinstrument_api():
        from QuantumDesign.QDInstrument import (
            QDInstrumentBase,
            QDInstrumentFactory,
        )

        return QDInstrumentBase, QDInstrumentFactory

    @classmethod
    def _load_qdinstrument_api(cls, clr, explicit_path: str = ""):
        """Load LabVIEW's matching assembly by path or .NET assembly name."""
        attempts: list[str] = []
        for dll in cls.discover_dll_paths(explicit_path):
            try:
                clr.AddReference(str(dll))
                api = cls._import_qdinstrument_api()
                return (*api, str(dll))
            except BaseException as error:
                attempts.append(f"{dll}: {error}")

        # QDInstrument may be installed in the GAC or another .NET probing
        # location used by LabVIEW.  In that case no file path is required.
        try:
            clr.AddReference("QDInstrument")
            api = cls._import_qdinstrument_api()
            return (*api, ".NET auto-resolved: QDInstrument")
        except BaseException as error:
            attempts.append(f".NET assembly name QDInstrument: {error}")

        # Only after the fast candidates and the .NET loader fail, perform a
        # bounded recursive search.  connect() already runs off the Tk thread.
        for dll in cls.discover_dll_paths(
            explicit_path, recursive=True, time_limit_s=8.0
        ):
            try:
                clr.AddReference(str(dll))
                api = cls._import_qdinstrument_api()
                return (*api, str(dll))
            except BaseException as error:
                detail = f"{dll}: {error}"
                if detail not in attempts:
                    attempts.append(detail)

        explicit_note = ""
        if str(explicit_path).strip():
            explicit_note = f"\nManually entered path: {explicit_path}"
        attempt_text = "\n".join(f"  - {item}" for item in attempts[-8:])
        raise RuntimeError(
            "TCP 11000 being reachable only means QDInstrument_Server is listening; the local Python "
            "still needs the same QDInstrument.dll set as LabVIEW. The program tried loading by .NET assembly name, "
            "common program/LabVIEW directories and a time-limited automatic search, but did not succeed.\n"
            "Please locate QDInstrument.dll in an available LabVIEW project directory, or copy the DLL matching "
            "the LabVIEW/Server set into the main program directory."
            f"{explicit_note}\nLoad log:\n{attempt_text}"
        )

    @staticmethod
    def _normalize_readback(raw_result, quantity: str) -> tuple[float, str]:
        if isinstance(raw_result, tuple):
            values = list(raw_result)
        elif isinstance(raw_result, list):
            values = raw_result
        else:
            try:
                values = list(raw_result)
            except TypeError:
                values = [raw_result]
        if len(values) < 2:
            raise RuntimeError(
                f"QDInstrument {quantity} returned an unexpected format: {raw_result!r}"
            )
        # pythonnet returns ref/out arguments after an optional .NET return
        # value.  Therefore the physical value and status are always the last
        # two items for both void and integer-returning QDInstrument builds.
        physical_value, status = values[-2], values[-1]
        try:
            physical_value = float(physical_value)
        except (TypeError, ValueError) as error:
            raise RuntimeError(
                f"QDInstrument {quantity} reading is not numeric: {physical_value!r}"
            ) from error
        if not math.isfinite(physical_value):
            raise RuntimeError(
                f"QDInstrument {quantity} returned a non-finite reading: {physical_value!r}"
            )
        if len(values) >= 3:
            return_code = values[0]
            try:
                numeric_code = int(return_code)
            except (TypeError, ValueError):
                numeric_code = 0
            if numeric_code != 0:
                raise RuntimeError(
                    f"QDInstrument {quantity} read failed, return code {numeric_code}"
                )
        status_text = getattr(status, "name", None)
        if not status_text:
            try:
                status_text = status.ToString()
            except BaseException:
                status_text = str(status)
        return physical_value, str(status_text)

    @staticmethod
    def _reflection_signature(method) -> str:
        try:
            parameters = method.GetParameters()
            parts = []
            for parameter in parameters:
                parameter_type = parameter.ParameterType
                if bool(getattr(parameter_type, "IsByRef", False)):
                    parameter_type = parameter_type.GetElementType()
                    prefix = "ref "
                else:
                    prefix = ""
                type_name = getattr(parameter_type, "FullName", None)
                if not type_name:
                    type_name = str(parameter_type)
                parts.append(f"{prefix}{type_name}")
            return f"{method.Name}({', '.join(parts)})"
        except BaseException:
            return str(method)

    @classmethod
    def _invoke_ref_readback(cls, instrument, method_name: str):
        """Invoke QDInstrument ref/out readbacks through .NET reflection.

        QDInstrument releases expose GetTemperature/GetField with ref/out
        parameters, but their status argument changed between numeric and enum
        types.  Passing Python integers therefore fails overload resolution in
        some LabVIEW 2020 assemblies.  Reflection lets the loaded DLL tell us
        the exact argument types and returns the mutated ref/out values.
        """
        try:
            import System
        except ImportError as error:
            raise RuntimeError(
                "pythonnet loaded QDInstrument.dll, but the System reflection interface cannot be imported"
            ) from error

        try:
            methods = [
                method
                for method in instrument.GetType().GetMethods()
                if str(method.Name) == str(method_name)
            ]
        except BaseException as error:
            raise RuntimeError(
                f"Cannot reflect QDInstrument method {method_name}: {error}"
            ) from error
        if not methods:
            raise RuntimeError(
                f"The current QDInstrument.dll has no method {method_name}"
            )

        def rank(method) -> tuple[int, int]:
            try:
                parameters = list(method.GetParameters())
            except BaseException:
                return (99, 99)
            ref_count = sum(
                bool(getattr(item.ParameterType, "IsByRef", False))
                for item in parameters
            )
            # Official QD readbacks use exactly two ref/out parameters:
            # physical value and state.  Prefer that shape deterministically.
            return (0 if len(parameters) == 2 else 1, -ref_count)

        errors: list[str] = []
        for method in sorted(methods, key=rank):
            signature = cls._reflection_signature(method)
            try:
                parameters = list(method.GetParameters())
                if len(parameters) != 2:
                    errors.append(f"{signature}: parameter count is not 2")
                    continue
                arguments = System.Array[System.Object](len(parameters))
                for index, parameter in enumerate(parameters):
                    parameter_type = parameter.ParameterType
                    if bool(getattr(parameter_type, "IsByRef", False)):
                        value_type = parameter_type.GetElementType()
                    else:
                        value_type = parameter_type
                    if bool(getattr(value_type, "IsEnum", False)):
                        value = System.Enum.ToObject(value_type, 0)
                    elif bool(getattr(value_type, "IsValueType", False)):
                        value = System.Activator.CreateInstance(value_type)
                    else:
                        value = None
                    arguments[index] = value
                return_value = method.Invoke(instrument, arguments)
                output_values = list(arguments)
                if return_value is not None:
                    output_values.insert(0, return_value)
                return tuple(output_values)
            except BaseException as error:
                errors.append(f"{signature}: {error}")
        details = "\n".join(f"  - {item}" for item in errors)
        raise RuntimeError(
            f"QDInstrument {method_name} ref/out call failed:\n{details}"
        )

    @classmethod
    def _invoke_typed_command(cls, instrument, method_name: str, values):
        """Invoke a QD command using the exact value/enum types in its DLL."""
        try:
            import System
        except ImportError as error:
            raise RuntimeError(
                "pythonnet loaded QDInstrument.dll, but the System reflection interface cannot be imported"
            ) from error
        methods = [
            method
            for method in instrument.GetType().GetMethods()
            if str(method.Name) == str(method_name)
        ]
        if not methods:
            raise RuntimeError(f"The current QDInstrument.dll has no method {method_name}")
        errors: list[str] = []
        for method in methods:
            invoked = False
            signature = cls._reflection_signature(method)
            try:
                parameters = list(method.GetParameters())
                if len(parameters) != len(values):
                    continue
                arguments = System.Array[System.Object](len(parameters))
                for index, (parameter, raw_value) in enumerate(
                    zip(parameters, values)
                ):
                    value_type = parameter.ParameterType
                    if bool(getattr(value_type, "IsByRef", False)):
                        value_type = value_type.GetElementType()
                    if bool(getattr(value_type, "IsEnum", False)):
                        converted = System.Enum.ToObject(
                            value_type, int(raw_value)
                        )
                    else:
                        try:
                            converted = System.Convert.ChangeType(
                                raw_value,
                                value_type,
                                System.Globalization.CultureInfo.InvariantCulture,
                            )
                        except BaseException:
                            converted = value_type(raw_value)
                    arguments[index] = converted
                invoked = True
                return_value = method.Invoke(instrument, arguments)
                # Boolean success and numeric status codes are different contracts.
                # Never reinterpret an undocumented integer 1 as success.
                return_type = getattr(method, "ReturnType", None)
                if str(getattr(return_type, "FullName", "")) == "System.Boolean":
                    if not bool(return_value):
                        raise RuntimeError("returned Boolean False")
                elif return_value is not None and int(return_value) != 0:
                    if int(return_value) == 1 and method_name in ("SetTemperature", "SetField"):
                        raise QDCommandUncertain(f"{method_name} return code 1; the command may have taken effect, not resending")
                    raise RuntimeError(
                        f"return code {int(return_value)} (return type {return_type}); "
                        "the command may have taken effect; please verify the MultiVu target and rate and do not resubmit"
                    )
                return return_value
            except BaseException as error:
                if invoked:
                    # Never try another overload after a physical write attempt.
                    raise
                errors.append(f"{signature}: {error}")
        details = "\n".join(f"  - {item}" for item in errors)
        raise RuntimeError(
            f"QDInstrument {method_name} call failed:\n{details}"
        )

    def connect(self, host: str, port: int, dll_path: str = "") -> dict[str, object]:
        host, port = PPMSMultiVuReadbackController.validate_endpoint(host, port)
        with self.lock:
            if self.instrument is not None:
                return self.read_environment()
            if not sys.platform.startswith("win"):
                raise RuntimeError("The QDInstrument remote interface can only be loaded on Windows")
            try:
                import clr
            except ImportError as error:
                raise RuntimeError(
                    "The current Python lacks pythonnet; please run:\n"
                    "py -m pip install pythonnet"
                ) from error
            try:
                QDInstrumentBase, QDInstrumentFactory, load_source = (
                    self._load_qdinstrument_api(clr, dll_path)
                )

                instrument = QDInstrumentFactory.GetQDInstrument(
                    QDInstrumentBase.QDInstrumentType.PPMS,
                    True,
                    host,
                    int(port),
                )
            except BaseException as error:
                raise RuntimeError(
                    "Loading QDInstrument.dll or creating the remote PPMS session failed. Please confirm the DLL is not "
                    "blocked by Windows, that .NET Framework 3.5 is enabled on the ThinkPad, and that the same set of "
                    f"QDInstrument files as the lab PC is used. Original error: {error}"
                ) from error
            self.instrument = instrument
            self.host = host
            self.port = port
            self.dll_path = str(load_source)
            try:
                result = self.read_environment()
            except BaseException:
                self.disconnect()
                raise
            self.logger(
                f"PPMS QDInstrument connected: {host}:{port}; assembly={load_source}"
            )
            return result

    def read_environment(self) -> dict[str, object]:
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("PPMS QDInstrument is not connected")
            temperature_raw = self._invoke_ref_readback(
                self.instrument, "GetTemperature"
            )
            field_raw = self._invoke_ref_readback(self.instrument, "GetField")
        temperature_k, temperature_status = self._normalize_readback(
            temperature_raw, "temperature"
        )
        field_oe, field_status = self._normalize_readback(field_raw, "magnetic field")
        data = {
            "temperature_k": temperature_k,
            "temperature_status": temperature_status,
            "field_oe": field_oe,
            "field_status": field_status,
        }
        self._verify_environment_command(data)
        return data

    def _verify_environment_command(self, data):
        pending = getattr(self, "pending_environment_command", None)
        if not pending:
            return
        kind, target, deadline = pending
        temperature = kind == "SetTemperature"
        label = "Temperature" if temperature else "Magnetic field"
        measured = float(data["temperature_k" if temperature else "field_oe"])
        status = str(data["temperature_status" if temperature else "field_status"]).lower().replace(" ", "")
        stable = (status in ("stable", "stableattarget") if temperature else
                  status in ("stable", "holding", "persistent", "stablepersistent", "stabledriven", "standby"))
        tolerance = .1 if temperature else max(PPMS_FIELD_TOLERANCE_OE, abs(target)*PPMS_FIELD_RELATIVE_TOLERANCE)
        if stable and abs(measured-target) <= tolerance:
            text = f"{label} reply uncertain; readback now confirms the target {target:g} was reached and is stable (rate not confirmed by readback)"
            self.pending_environment_command = None
        elif time.monotonic() >= deadline:
            text = f"{label} readback confirmation timed out; current {measured:g}, target {target:g}, please check MultiVu; not resent"
            self.pending_environment_command = None
        else:
            text = f"{label} reply uncertain, confirming by readback; target {target:g}, current {measured:g}; not resent"
        self.environment_command_status = text
        data["command_verification"] = text

    def _send_environment_command(self, name, values):
        self.pending_environment_command = None
        try:
            self._invoke_typed_command(self.instrument, name, values)
        except QDCommandUncertain as error:
            self.logger(str(error))
            self.pending_environment_command = (name, float(values[0]), time.monotonic() + (14400 if name == "SetTemperature" else 3600))

    def set_temperature(
        self, target_k: float, rate_k_min: float, approach_code: int
    ) -> dict[str, object]:
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("PPMS QDInstrument is not connected")
            current = self.read_environment()
            target_k, rate_k_min = validate_ppms_temperature_command(
                target_k, rate_k_min, float(current["temperature_k"])
            )
            if int(approach_code) not in (0, 1):
                raise ValueError("PPMS temperature approach parameter is invalid")
            self._send_environment_command(
                "SetTemperature",
                (target_k, rate_k_min, int(approach_code)),
            )
            self.logger(
                f"PPMS temperature setpoint: {target_k:g} K, {rate_k_min:g} K/min"
            )
            return self.read_environment()

    def set_field(
        self,
        target_oe: float,
        rate_oe_s: float,
        approach_code: int,
        mode_code: int,
    ) -> dict[str, object]:
        target_oe, rate_oe_s = validate_ppms_field_command(target_oe, rate_oe_s)
        if int(approach_code) not in (0, 1, 2):
            raise ValueError("PPMS field approach parameter is invalid")
        if int(mode_code) not in (0, 1):
            raise ValueError("PPMS field end mode parameter is invalid")
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("PPMS QDInstrument is not connected")
            self._send_environment_command(
                "SetField",
                (
                    target_oe,
                    rate_oe_s,
                    int(approach_code),
                    int(mode_code),
                ),
            )
            self.logger(
                f"PPMS field setpoint: {target_oe:+g} Oe, "
                f"{rate_oe_s:g} Oe/s，mode={int(mode_code)}"
            )
            return self.read_environment()

    def disconnect(self) -> None:
        with self.lock:
            instrument = self.instrument
            self.instrument = None
            self.host = ""
            self.port = 0
            self.dll_path = ""
            if instrument is None:
                return
            for method_name in ("Dispose", "Close"):
                method = getattr(instrument, method_name, None)
                if callable(method):
                    try:
                        method()
                    except BaseException:
                        pass
                    break
            self.logger(
                "PPMS QDInstrument client disconnected (current setpoints unchanged)"
            )


class PPMSScanCancelled(RuntimeError):
    """Requested cancellation, distinct from device or cleanup failure."""


class PPMSMotorDebug:
    """QDInstrument position probe with one explicit index-recovery resend.

    Not an encoder or emergency stop. Keep MultiVu Motion Control available.
    PPMS uses GetPPMSItem and SendPPMSCommand, not DynaCool position APIs.
    Only normal MOVE mode 0 is supported; no index/define/configuration writes.
    """

    AXIS = "Horizontal Rotator"

    def __init__(self, controller):
        self.controller = controller

    def _method(self, name, count):
        instrument = self.controller.instrument
        if instrument is None:
            raise RuntimeError("Please connect QDInstrument first")
        methods = [m for m in instrument.GetType().GetMethods()
                   if str(m.Name) == name and len(list(m.GetParameters())) == count]
        if len(methods) != 1:
            raise RuntimeError(f"{name}: no unique compatible interface found; please use 'Interface info' and send the log")
        return methods[0]

    def describe(self):
        with self.controller.lock:
            if self.controller.instrument is None:
                raise RuntimeError("Please connect QDInstrument first")
            lines = []
            for method in self.controller.instrument.GetType().GetMethods():
                if any(word in str(method.Name).lower() for word in ("position", "ppmsitem", "ppmscommand")):
                    lines.append(str(method))
                    for parameter in method.GetParameters():
                        typ = parameter.ParameterType
                        if getattr(typ, "IsByRef", False):
                            typ = typ.GetElementType()
                        if getattr(typ, "IsEnum", False):
                            import System
                            lines.append(f"  {typ}: " + ", ".join(str(n) for n in System.Enum.GetNames(typ)))
            return "\n".join(lines) or "No motor-related interface found"

    @staticmethod
    def _check_return(method, value):
        typ = str(getattr(getattr(method, "ReturnType", None), "FullName", ""))
        if typ == "System.Boolean":
            good = bool(value)
        else:
            good = value is None or int(value) == 0
        if not good:
            raise RuntimeError(f"{method}: returned {value!r} ({typ}); not resending, please verify in MultiVu whether it was executed")

    def _read_item(self, channel):
        import System
        with self.controller.lock:
            # QD application note 1070-210 C0 pp.5-6: GetPosition is
            # DynaCool-only; PPMS motor angle is GetPPMSItem channel 3.
            method = self._method("GetPPMSItem", 3)
            params = list(method.GetParameters())
            if (str(params[0].ParameterType.FullName) != "System.Int32"
                    or not getattr(params[1].ParameterType, "IsByRef", False)
                    or str(params[1].ParameterType.GetElementType().FullName) != "System.Double"
                    or str(params[2].ParameterType.FullName) != "System.Boolean"):
                raise RuntimeError(f"GetPPMSItem signature incompatible: {method}")
            arguments = System.Array[System.Object](3)
            arguments[0] = System.Int32(channel)
            arguments[1] = System.Double(0)
            arguments[2] = System.Boolean(True)
            result = method.Invoke(self.controller.instrument, arguments)
            self.controller.logger(f"PPMS motor GetPPMSItem({channel}): return={result!r}; value={arguments[1]!r}")
            self._check_return(method, result)
            value = float(arguments[1])
            if not math.isfinite(value):
                raise RuntimeError("PPMS data item is not a finite value")
            return value

    def read(self):
        with self.controller.lock:
            angle = self._read_item(3)
            status = "PositionUnknown"
            try:
                word = self._read_item(0)
                if not word.is_integer() or not 0 <= word <= 65535:
                    raise ValueError(f"Invalid status word: {word}")
                code = (int(word) >> 12) & 15
                status = {1: "AtTarget", 5: "Moving", 8: "AtLimitSwitch", 9: "AtIndexSwitch", 15: "PositionFailure"}.get(code, "PositionUnknown")
            except Exception as error:
                self.controller.logger(f"PPMS motor status unavailable: {error}")
            if not math.isfinite(angle):
                raise RuntimeError("Motor angle readback is not finite")
            self.controller.logger(f"PPMS motor: angle={angle:.6f} deg; status={status}")
            data = {"angle_deg": angle, "status": status}
            on_read = getattr(self, "on_read", None)
            if on_read is not None:
                on_read(dict(data))
            return data

    @staticmethod
    def validate_move(target, speed, current, allow_large=False):
        target, speed = float(target), float(speed)
        if not all(math.isfinite(v) for v in (target, speed, float(current))):
            raise ValueError("Motor parameters must be finite values")
        # The input range limits commanded targets, not measured coordinates.
        if not -370 <= target <= 370:
            raise ValueError("PPMS target angle must be within −370 to 370°")
        if abs(target - current) > 370.0 and not allow_large:
            raise ValueError("Each move must not exceed 370°; please enter the target based on the latest angle")
        if not speed.is_integer() or not 0 <= speed <= 14:
            raise ValueError("Deceleration code must be an integer 0–14; 0 fastest, 14 slowest")
        return target, int(speed)

    def move(self, target, speed, allow_large=False, _index_retry=False):
        import System
        self.movement_attempted = False
        with self.controller.lock:
            before = self.read()
            target, slowdown = self.validate_move(target, speed, before["angle_deg"], allow_large)
            if before["status"] not in ("AtTarget", "AtIndexSwitch"):
                raise RuntimeError(f"Motion status is not AtTarget: {before['status']}; MOVE not sent")
            method = self._method("SendPPMSCommand", 5)
            params = list(method.GetParameters())
            types_ = [p.ParameterType for p in params]
            expected = ["System.String", "System.String", "System.String", "System.Int32", "System.Double"]
            actual = [str((t.GetElementType() if getattr(t, "IsByRef", False) else t).FullName) for t in types_]
            if actual != expected or [bool(getattr(t, "IsByRef", False)) for t in types_] != [False, True, True, False, False]:
                raise RuntimeError(f"SendPPMSCommand signature incompatible: {method}")
            command = f"MOVE {target:.6f} 0 {slowdown}"
            args = System.Array[System.Object](5)
            args[0], args[1], args[2] = command, "", ""
            args[3], args[4] = System.Int32(0), System.Double(0)
            self.controller.logger(f"PPMS motor SEND ONCE: {command}")
            self.movement_attempted = True
            result = method.Invoke(self.controller.instrument, args)
            self.controller.logger(f"PPMS motor reply: return={result!r}; reply={args[1]!r}; error={args[2]!r}")
            # This PPMS returned Int32(1) on a confirmed successful MOVE.
            # Treat it as ambiguous submission, never as unconditional success.
            return_type = str(getattr(method.ReturnType, "FullName", ""))
            ambiguous = return_type == "System.Int32" and int(result) == 1
            if not ambiguous:
                self._check_return(method, result)
            if str(args[1] or "").strip() or str(args[2] or "").strip():
                ambiguous = True
                self.controller.logger("MOVE reply is non-empty, execution result pending readback confirmation; MOVE not resent")
            return {"target_deg": target, "return_code": result, "ambiguous": ambiguous,
                    "raw_reply": str(args[1] or ""), "raw_error": str(args[2] or "")}

    def move_and_verify(self, target, speed, cancelled=lambda: False,
                        timeout=90.0, interval=0.5, allow_large=False,
                        index_target=None):
        # index_target remains accepted for compatibility; Index never resends MOVE.
        if cancelled():
            raise PPMSScanCancelled("Cancelled before moving, MOVE not sent; this does not mean the motor is stopped")
        submission = self.move(target, speed, allow_large=True) if allow_large else self.move(target, speed)
        deadline = time.monotonic() + timeout
        consecutive = 0
        last = None
        while time.monotonic() < deadline:
            if cancelled():
                raise PPMSScanCancelled("Arrival readback cancelled; this does not mean the motor is stopped, please check MultiVu")
            try:
                last = self.read()
            except Exception as error:
                raise RuntimeError(f"MOVE was sent, but the arrival readback failed and the motion status is unconfirmed; MOVE not resent, please check MultiVu: {error}") from error
            if last["status"] in ("AtLimitSwitch", "PositionFailure", "PositionUnknown"):
                raise RuntimeError(f"Abnormal motor status: {last}; MOVE not resent, please check MultiVu")
            if last["status"] in ("AtTarget", "AtIndexSwitch") and abs(last["angle_deg"] - submission["target_deg"]) <= 0.06:
                consecutive += 1
                if consecutive >= 2:
                    self.controller.logger(f"PPMS motor VERIFIED: {last}; submission={submission}")
                    return dict(last, **submission)
            else:
                consecutive = 0
            time.sleep(interval)
        raise RuntimeError(f"Motor arrival confirmation timed out ({timeout:g}s); last readback {last}; MOVE not resent, please check MultiVu")


class PPMSRemoteReadbackController:
    """Select one Quantum Design remote backend per PPMS connection."""

    def __init__(self, logger: Callable[[str], None]) -> None:
        self.logger = logger
        self.active = None
        self.backend = ""
        self.lock = threading.RLock()

    @property
    def connected(self) -> bool:
        return self.active is not None and self.active.connected

    def connect(
        self,
        backend: str,
        host: str,
        port: int,
        dll_path: str = "",
    ) -> dict[str, object]:
        with self.lock:
            if self.connected:
                return self.active.read_environment()
            if backend == PPMS_QDINSTRUMENT_BACKEND:
                controller = PPMSQDInstrumentReadbackController(self.logger)
                result = controller.connect(host, port, dll_path)
            elif backend == PPMS_MULTIPYVU_BACKEND:
                controller = PPMSMultiVuReadbackController(self.logger)
                result = controller.connect(host, port)
            else:
                raise ValueError(f"Unknown PPMS interface: {backend}")
            self.active = controller
            self.backend = backend
            return result

    def read_environment(self) -> dict[str, object]:
        with self.lock:
            if not self.connected:
                raise RuntimeError("PPMS remote interface is not connected")
            return self.active.read_environment()

    def set_temperature(
        self, target_k: float, rate_k_min: float, approach_code: int
    ) -> dict[str, object]:
        with self.lock:
            if not self.connected:
                raise RuntimeError("PPMS remote interface is not connected")
            return self.active.set_temperature(
                target_k, rate_k_min, approach_code
            )

    def set_field(
        self,
        target_oe: float,
        rate_oe_s: float,
        approach_code: int,
        mode_code: int,
    ) -> dict[str, object]:
        with self.lock:
            if not self.connected:
                raise RuntimeError("PPMS remote interface is not connected")
            return self.active.set_field(
                target_oe, rate_oe_s, approach_code, mode_code
            )

    def disconnect(self) -> None:
        with self.lock:
            controller = self.active
            self.active = None
            self.backend = ""
            if controller is not None:
                controller.disconnect()


def validate_ppms_temperature_command(
    target_k: float,
    rate_k_min: float,
    current_k: float | None = None,
) -> tuple[float, float]:
    """Validate PPMS temperature and return (target K, rate K/min).

    Any command whose path enters the 5–9 K interval (inclusive) is limited to
    1 K/min. Commands wholly above 9 K may use up to 10 K/min.
    This conservative whole-ramp limit avoids changing rate mid-ramp.
    """
    target_k = float(target_k)
    rate_k_min = float(rate_k_min)
    if not math.isfinite(target_k) or not math.isfinite(rate_k_min):
        raise ValueError("PPMS target temperature and ramp rate must be finite values")
    if not PPMS_TEMPERATURE_MIN_K <= target_k <= PPMS_TEMPERATURE_MAX_K:
        raise ValueError(
            f"PPMS target temperature must be within {PPMS_TEMPERATURE_MIN_K:g}–"
            f"{PPMS_TEMPERATURE_MAX_K:g} K"
        )
    if rate_k_min <= 0:
        raise ValueError("PPMS temperature ramp rate must be greater than 0 K/min")
    current_valid = current_k is not None and math.isfinite(float(current_k))
    enters_low_range = target_k <= PPMS_TEMPERATURE_LOW_BOUNDARY_K
    if current_valid:
        enters_low_range = enters_low_range or (
            float(current_k) <= PPMS_TEMPERATURE_LOW_BOUNDARY_K
        )
    maximum = (
        PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN
        if enters_low_range
        else PPMS_TEMPERATURE_MAX_RATE_K_MIN
    )
    if rate_k_min > maximum + 1e-12:
        region = "within the 5–9 K range" if enters_low_range else "above 9 K"
        raise ValueError(
            f"PPMS temperature ramp rate {region} must not exceed {maximum:g} K/min"
        )
    return target_k, rate_k_min


def validate_ppms_field_command(
    target_oe: float,
    rate_oe_s: float,
) -> tuple[float, float]:
    """Validate the PPMS field guardrails used by all field commands."""
    target_oe = float(target_oe)
    rate_oe_s = float(rate_oe_s)
    if not math.isfinite(target_oe) or not math.isfinite(rate_oe_s):
        raise ValueError("PPMS target field and field ramp rate must be finite values")
    if abs(target_oe) > PPMS_FIELD_LIMIT_OE:
        raise ValueError(
            f"PPMS target field must be within ±{PPMS_FIELD_LIMIT_OE:g} Oe"
        )
    if rate_oe_s <= 0 or rate_oe_s >= PPMS_FIELD_RATE_LIMIT_OE_S:
        raise ValueError(
            f"PPMS field ramp rate must be greater than 0 and below "
            f"{PPMS_FIELD_RATE_LIMIT_OE_S:g} Oe/s"
        )
    return target_oe, rate_oe_s


class Keithley6221Controller:
    """Thread-safe 6221 continuous AC/DC and unipolar-pulse controller.

    This is deliberately not SOUR:PDEL (Pulse Delta): the official Pulse Delta
    arming sequence requires a Keithley 2182A.  A 50% duty square waveform with
    amplitude=abs(I)/2 and offset=I/2 produces a standalone 0 A <-> I DC pulse
    train.  The requested pulse width is the high time, hence f=1/(2*width),
    and WAVE:DUR:CYCL limits the output to the requested integer pulse count.
    """

    def __init__(self, logger: Callable[[str], None]) -> None:
        self.logger = logger
        self.resource_manager = None
        self.instrument = None
        self.resource_name = ""
        self.identity = ""
        self.output_enabled = False
        self.current_a = 0.0
        self.pulse_width_s = K6221_DEFAULT_PULSE_WIDTH_US / 1_000_000.0
        self.pulse_count = K6221_DEFAULT_PULSE_COUNT
        self.compliance_v = K6221_DEFAULT_COMPLIANCE_V
        self.output_mode = "OFF"
        self.lock = threading.RLock()

    @property
    def connected(self) -> bool:
        return self.instrument is not None

    def _query_error(self, command: str) -> None:
        response = self.instrument.query("SYST:ERR?").strip()
        try:
            code = int(response.split(",", 1)[0].strip())
        except ValueError as error:
            raise RuntimeError(f"6221 returned an unparsable error status: {response}") from error
        if code != 0:
            raise RuntimeError(f"6221 SCPI error after '{command}': {response}")

    def _write_checked(self, command: str) -> None:
        self.instrument.write(command)
        self._query_error(command)

    def _read_output_low_to_earth(self) -> int:
        """1 when output low is tied to earth ground, 0 when it floats."""
        text = self.instrument.query(K6221_OUTPUT_LOW_QUERY).strip().upper()
        if text in ("0", "OFF"):
            return 0
        if text in ("1", "ON"):
            return 1
        raise RuntimeError(f"6221 returned an unexpected OUTP:LTE state: {text!r}")

    def _set_output_low_floating(self) -> None:
        """Front panel TRIAX -> OUTPUT LOW -> FLOATING, confirmed by readback."""
        if self._read_output_low_to_earth() == 0:
            return
        self._write_checked(K6221_OUTPUT_LOW_FLOATING)
        if self._read_output_low_to_earth() != 0:
            raise RuntimeError("6221 output low stayed earth-grounded; "
                               "set TRIAX -> OUTPUT LOW -> FLOATING on the front panel")

    @staticmethod
    def _validate(
        current_a: float,
        pulse_width_s: float,
        compliance_v: float,
        pulse_count: int,
    ):
        current_a = float(current_a)
        pulse_width_s = float(pulse_width_s)
        compliance_v = float(compliance_v)
        try:
            pulse_count_numeric = float(pulse_count)
        except (TypeError, ValueError) as error:
            raise ValueError("6221 pulse count must be an integer") from error
        if not math.isfinite(pulse_count_numeric) or not pulse_count_numeric.is_integer():
            raise ValueError("6221 pulse count must be an integer")
        pulse_count = int(pulse_count_numeric)
        if not all(
            math.isfinite(value)
            for value in (current_a, pulse_width_s, compliance_v)
        ):
            raise ValueError("6221 current, pulse width and compliance voltage must be finite values")
        if abs(current_a) > K6221_MAX_ABS_CURRENT_A:
            raise ValueError(
                f"6221 current exceeds the software limit ±{K6221_MAX_ABS_CURRENT_A * 1000:g} mA"
            )
        if 0.0 < abs(current_a) < K6221_MIN_NONZERO_CURRENT_A:
            raise ValueError("6221 non-zero pulse current magnitude cannot be less than 4 pA")
        if not K6221_MIN_PULSE_WIDTH_S <= pulse_width_s <= K6221_MAX_PULSE_WIDTH_S:
            raise ValueError(
                "6221 pulse width must be between "
                f"{K6221_MIN_PULSE_WIDTH_S * 1e6:g} us–"
                f"{K6221_MAX_PULSE_WIDTH_S:g} s"
            )
        if not K6221_MIN_COMPLIANCE_V <= compliance_v <= K6221_MAX_COMPLIANCE_V:
            raise ValueError(
                f"6221 compliance voltage must be between {K6221_MIN_COMPLIANCE_V:g}–"
                f"{K6221_MAX_COMPLIANCE_V:g} V"
            )
        if not 1 <= pulse_count <= K6221_MAX_PULSE_COUNT:
            raise ValueError(
                f"6221 pulse count must be between 1 and {K6221_MAX_PULSE_COUNT}"
            )
        total_time_s = 2.0 * pulse_width_s * pulse_count
        if total_time_s > K6221_MAX_WAVEFORM_TIME_S:
            raise ValueError(
                "Total waveform time (pulse width × pulse count) exceeds "
                f"{K6221_MAX_WAVEFORM_TIME_S:g} s"
            )
        return current_a, pulse_width_s, compliance_v, pulse_count

    def connect(self, resource_name: str, compliance_v: float) -> str:
        if pyvisa is None:
            raise RuntimeError("pyvisa is missing; please run: py -m pip install pyvisa pyvisa-py")
        _, _, compliance_v, _ = self._validate(
            0.0, self.pulse_width_s, compliance_v, self.pulse_count
        )
        with self.lock:
            if self.instrument is not None:
                return self.identity
            acquire_instrument_lease(resource_name, self, "Keithley 6221 pulse")
            manager = None
            instrument = None
            try:
                manager = acquire_shared_visa_manager()
                instrument = manager.open_resource(resource_name)
                instrument.timeout = 5000
                instrument.write_termination = "\n"
                instrument.read_termination = "\n"
                identity = instrument.query("*IDN?").strip()
                if "6221" not in identity.upper():
                    raise RuntimeError(
                        f"The device returned by {resource_name} is not a Keithley 6221: {identity}"
                    )
                self.resource_manager = manager
                self.instrument = instrument
                self.resource_name = resource_name
                self.identity = identity
                self.compliance_v = compliance_v
                instrument.write("*RST")
                instrument.write("*CLS")
                self._set_output_low_floating()
                self._write_checked(f"CURR:COMP {compliance_v:.12g}")
                self._write_checked("OUTP OFF")
                self.output_enabled = False
                self.current_a = 0.0
                self.output_mode = "OFF"
                self.logger(f"Keithley 6221 connected: {identity}")
                return identity
            except BaseException:
                if instrument is not None:
                    for command in ("SOUR:WAVE:ABOR", "CURR 0", "OUTP OFF"):
                        try:
                            instrument.write(command)
                        except BaseException:
                            pass
                    try:
                        instrument.close()
                    except BaseException:
                        pass
                release_shared_visa_manager(manager)
                self.resource_manager = None
                self.instrument = None
                release_instrument_lease(resource_name, self)
                raise

    def set_continuous_output(
        self,
        mode: str,
        current_a: float,
        frequency_hz: float,
        compliance_v: float,
    ) -> dict[str, object]:
        """Apply continuous sine AC (GUI RMS) or fixed DC output."""
        mode = str(mode).strip().upper()
        current_a = float(current_a)
        frequency_hz = float(frequency_hz)
        compliance_v = float(compliance_v)
        if mode not in {"AC", "DC"}:
            raise ValueError("6221 continuous output mode can only be AC or DC")
        if not all(
            math.isfinite(value)
            for value in (current_a, frequency_hz, compliance_v)
        ):
            raise ValueError("6221 continuous output parameters must be finite values")
        if not K6221_MIN_COMPLIANCE_V <= compliance_v <= K6221_MAX_COMPLIANCE_V:
            raise ValueError(
                f"6221 compliance voltage must be between {K6221_MIN_COMPLIANCE_V:g}–"
                f"{K6221_MAX_COMPLIANCE_V:g} V"
            )
        if mode == "AC":
            if current_a < 0:
                raise ValueError("6221 AC current uses the RMS amplitude and cannot be negative")
            peak_a = math.sqrt(2.0) * current_a
            if peak_a > K6221_MAX_ABS_CURRENT_A:
                raise ValueError(
                    "6221 AC peak exceeds the hardware range; "
                    f"RMS maximum is about {K6221_MAX_ABS_CURRENT_A / math.sqrt(2) * 1000:g} mA"
                )
            if 0.0 < current_a < K6221_MIN_NONZERO_CURRENT_A:
                raise ValueError("6221 non-zero AC RMS current cannot be less than 4 pA")
            if not 1e-3 <= frequency_hz <= 1e5:
                raise ValueError("6221 AC frequency must be within 0.001–100000 Hz")
        else:
            peak_a = 0.0
            if abs(current_a) > K6221_MAX_ABS_CURRENT_A:
                raise ValueError(
                    f"6221 DC current exceeds ±{K6221_MAX_ABS_CURRENT_A * 1000:g} mA"
                )
            if 0.0 < abs(current_a) < K6221_MIN_NONZERO_CURRENT_A:
                raise ValueError("6221 non-zero DC current magnitude cannot be less than 4 pA")

        with self.lock:
            if self.instrument is None:
                raise RuntimeError("Keithley 6221 is not connected")
            if current_a == 0.0:
                self.output_off()
                return self.read_output_state()
            if mode == "AC":
                commands = (
                    "SOUR:WAVE:ABOR",
                    f"CURR:COMP {compliance_v:.12g}",
                    "SOUR:WAVE:FUNC SIN",
                    f"SOUR:WAVE:FREQ {frequency_hz:.12g}",
                    f"SOUR:WAVE:AMPL {peak_a:.12e}",
                    "SOUR:WAVE:OFFS 0",
                    "SOUR:WAVE:RANG BEST",
                    "SOUR:WAVE:PMAR:STAT OFF",
                    "SOUR:WAVE:DUR:CYCL INF",
                    "SOUR:WAVE:ARM",
                    "OUTP ON",
                    "SOUR:WAVE:INIT",
                )
            else:
                commands = (
                    "SOUR:WAVE:ABOR",
                    f"CURR:COMP {compliance_v:.12g}",
                    "SOUR:CURR:RANG:AUTO ON",
                    f"SOUR:CURR {current_a:.12e}",
                    "OUTP ON",
                )
            for command in commands:
                self._write_checked(command)
            actual_compliance = float(
                self.instrument.query("CURR:COMP?").strip()
            )
            output_state = int(round(float(
                self.instrument.query("OUTP?").strip()
            )))
            if mode == "AC":
                actual_peak = float(
                    self.instrument.query("SOUR:WAVE:AMPL?").strip()
                )
                actual_frequency = float(
                    self.instrument.query("SOUR:WAVE:FREQ?").strip()
                )
                actual_current = actual_peak / math.sqrt(2.0)
                if not math.isclose(
                    actual_peak, peak_a, rel_tol=5e-4, abs_tol=1e-12
                ):
                    self.output_off()
                    raise RuntimeError(
                        "6221 AC amplitude readback mismatch: "
                        f"requested {peak_a:g} Apeak, readback {actual_peak:g} Apeak"
                    )
                if not math.isclose(
                    actual_frequency,
                    frequency_hz,
                    rel_tol=5e-4,
                    abs_tol=1e-6,
                ):
                    self.output_off()
                    raise RuntimeError(
                        "6221 AC frequency readback mismatch: "
                        f"requested {frequency_hz:g} Hz, readback {actual_frequency:g} Hz"
                    )
            else:
                actual_current = float(
                    self.instrument.query("SOUR:CURR?").strip()
                )
                actual_frequency = math.nan
                if not math.isclose(
                    actual_current, current_a, rel_tol=5e-4, abs_tol=1e-12
                ):
                    self.output_off()
                    raise RuntimeError(
                        "6221 DC current readback mismatch: "
                        f"requested {current_a:g} A, readback {actual_current:g} A"
                    )
            if not math.isclose(
                actual_compliance,
                compliance_v,
                rel_tol=5e-4,
                abs_tol=1e-6,
            ) or output_state != 1:
                self.output_off()
                raise RuntimeError(
                    "6221 continuous output start readback mismatch: "
                    f"compliance={actual_compliance:g} V, output={output_state}"
                )
            self.current_a = actual_current
            self.compliance_v = actual_compliance
            self.output_enabled = True
            self.output_mode = mode
            return self.read_output_state()

    def read_output_state(self) -> dict[str, object]:
        """Read source settings and real-time compliance condition (B3)."""
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("Keithley 6221 is not connected")
            output_on = bool(round(float(
                self.instrument.query("OUTP?").strip()
            )))
            compliance_v = float(
                self.instrument.query("CURR:COMP?").strip()
            )
            measurement_condition = int(round(float(
                self.instrument.query("STAT:MEAS:COND?").strip()
            )))
            mode = self.output_mode if output_on else "OFF"
            frequency_hz = math.nan
            if mode in {"AC", "PULSE"}:
                amplitude_a = float(
                    self.instrument.query("SOUR:WAVE:AMPL?").strip()
                )
                offset_a = float(
                    self.instrument.query("SOUR:WAVE:OFFS?").strip()
                )
                frequency_hz = float(
                    self.instrument.query("SOUR:WAVE:FREQ?").strip()
                )
                if mode == "AC":
                    current_a = amplitude_a / math.sqrt(2.0)
                else:
                    sign_source = offset_a if offset_a != 0 else 1.0
                    current_a = math.copysign(
                        abs(offset_a) + abs(amplitude_a), sign_source
                    )
            else:
                current_a = float(
                    self.instrument.query("SOUR:CURR?").strip()
                )
            self.output_enabled = output_on
            self.current_a = current_a if output_on else 0.0
            self.compliance_v = compliance_v
            if not output_on:
                self.output_mode = "OFF"
            return {
                "mode": mode,
                # This is the instrument's source-setting readback, not an
                # independent measurement of load current.
                "current_a": current_a,
                "frequency_hz": frequency_hz,
                "compliance_v": compliance_v,
                "output_on": output_on,
                "measurement_condition": measurement_condition,
                "in_compliance": bool(measurement_condition & 8),
                "sample_monotonic": time.monotonic(),
            }

    def set_dc_pulse(
        self,
        current_a: float,
        pulse_width_s: float,
        compliance_v: float,
        pulse_count: int,
    ) -> dict[str, object]:
        current_a, pulse_width_s, compliance_v, pulse_count = self._validate(
            current_a, pulse_width_s, compliance_v, pulse_count
        )
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("Keithley 6221 is not connected")
            if current_a == 0.0:
                self.output_off()
                return {
                    "mode": "OFF",
                    "current_a": 0.0,
                    "pulse_width_s": pulse_width_s,
                    "frequency_hz": 1.0 / (2.0 * pulse_width_s),
                    "compliance_v": compliance_v,
                    "pulse_count": pulse_count,
                    "total_time_s": 0.0,
                    "output_on": False,
                    "measurement_condition": 0,
                    "in_compliance": False,
                    "sample_monotonic": time.monotonic(),
                }
            frequency_hz = 1.0 / (2.0 * pulse_width_s)
            amplitude_a = abs(current_a) / 2.0
            offset_a = current_a / 2.0
            commands = (
                "SOUR:WAVE:ABOR",
                f"CURR:COMP {compliance_v:.12g}",
                "SOUR:WAVE:FUNC SQU",
                "SOUR:WAVE:DCYC 50",
                f"SOUR:WAVE:AMPL {amplitude_a:.12e}",
                f"SOUR:WAVE:OFFS {offset_a:.12e}",
                f"SOUR:WAVE:FREQ {frequency_hz:.12g}",
                "SOUR:WAVE:RANG BEST",
                "SOUR:WAVE:PMAR:STAT OFF",
                # Standalone pulse output is software-triggered. Explicitly
                # clear any external-trigger mode left by an earlier workflow
                # so INIT starts immediately instead of waiting on TLINK.
                "SOUR:WAVE:EXTR OFF",
                f"SOUR:WAVE:DUR:CYCL {pulse_count}",
                "SOUR:WAVE:ARM",
                "OUTP ON",
                "SOUR:WAVE:INIT",
            )
            for command in commands:
                self._write_checked(command)
            actual_amplitude = float(self.instrument.query("SOUR:WAVE:AMPL?").strip())
            actual_offset = float(self.instrument.query("SOUR:WAVE:OFFS?").strip())
            actual_frequency = float(self.instrument.query("SOUR:WAVE:FREQ?").strip())
            actual_compliance = float(self.instrument.query("CURR:COMP?").strip())
            actual_cycles = int(round(float(
                self.instrument.query("SOUR:WAVE:DUR:CYCL?").strip()
            )))
            output_state = int(round(float(self.instrument.query("OUTP?").strip())))
            measurement_condition = int(round(float(
                self.instrument.query("STAT:MEAS:COND?").strip()
            )))
            checks = (
                ("amplitude", actual_amplitude, amplitude_a, 5e-4, 1e-12),
                ("offset", actual_offset, offset_a, 5e-4, 1e-12),
                ("frequency", actual_frequency, frequency_hz, 5e-4, 1e-6),
                ("compliance voltage", actual_compliance, compliance_v, 5e-4, 1e-6),
            )
            for label, actual, requested, rel_tol, abs_tol in checks:
                if not math.isfinite(actual) or not math.isclose(
                    actual, requested, rel_tol=rel_tol, abs_tol=abs_tol
                ):
                    self.output_off()
                    raise RuntimeError(
                        f"6221 {label} readback mismatch: requested {requested:g}, "
                        f"readback {actual:g}"
                    )
            if actual_cycles != pulse_count:
                self.output_off()
                raise RuntimeError(
                    "6221 pulse count readback mismatch: "
                    f"requested {pulse_count}, readback {actual_cycles}"
                )
            # A finite waveform turns the 6221 output off automatically when
            # its programmed duration expires.  For a microsecond one-shot,
            # the train can therefore finish before OUTP? is queried.  ARM
            # and INIT have already passed SYST:ERR? checks, and all waveform
            # parameters plus the cycle count were verified above, so
            # OUTP=0 here means "completed before readback", not "failed to
            # start".  A real configuration mismatch is still rejected.
            completed_before_readback = output_state == 0
            self.current_a = math.copysign(
                abs(actual_offset) + abs(actual_amplitude), current_a
            )
            self.pulse_width_s = 1.0 / (2.0 * actual_frequency)
            self.compliance_v = actual_compliance
            self.pulse_count = pulse_count
            self.output_enabled = bool(output_state)
            self.output_mode = "PULSE" if output_state else "OFF"
            return {
                "mode": "PULSE",
                "current_a": self.current_a,
                "pulse_width_s": self.pulse_width_s,
                "frequency_hz": actual_frequency,
                "compliance_v": actual_compliance,
                "pulse_count": pulse_count,
                "total_time_s": 2.0 * self.pulse_width_s * pulse_count,
                "output_on": bool(output_state),
                "completed_before_readback": completed_before_readback,
                "measurement_condition": measurement_condition,
                "in_compliance": bool(measurement_condition & 8),
                "sample_monotonic": time.monotonic(),
            }

    def output_off(self) -> None:
        with self.lock:
            if self.instrument is None:
                return
            errors = []
            for command in ("SOUR:WAVE:ABOR", "CURR 0", "OUTP OFF"):
                try:
                    self.instrument.write(command)
                except BaseException as error:
                    errors.append(f"{command}: {error}")
            self.output_enabled = False
            self.current_a = 0.0
            self.output_mode = "OFF"
            if errors:
                raise RuntimeError("6221 failed to turn output off: " + "; ".join(errors))

    def disconnect(self) -> None:
        with self.lock:
            instrument = self.instrument
            manager = self.resource_manager
            resource_name = self.resource_name
            if instrument is None and manager is None:
                return
            shutdown_error = None
            try:
                self.output_off()
            except BaseException as error:
                shutdown_error = error
            if instrument is not None:
                for command in ("*CLS", "SYST:LOC"):
                    try:
                        instrument.write(command)
                    except BaseException:
                        pass
                try:
                    instrument.close()
                except BaseException:
                    pass
            release_shared_visa_manager(manager)
            self.instrument = None
            self.resource_manager = None
            self.resource_name = ""
            self.identity = ""
            self.output_enabled = False
            self.current_a = 0.0
            release_instrument_lease(resource_name, self)
            self.logger("Keithley 6221 disconnected")
            if shutdown_error is not None:
                raise shutdown_error


# CombinedControlApp._ppms_field_is_ready of the main program, as a function.
def ppms_field_is_ready(result: dict[str, object], target_oe: float, driven: bool = False) -> bool:
    """At the target with a settled status.  ``driven``: after a Driven
    command, where a Persistent status is the state from before it (the
    switch has not warmed yet), so it is not settled."""
    measured = float(result["field_oe"])
    tolerance = max(PPMS_FIELD_TOLERANCE_OE, abs(target_oe) * PPMS_FIELD_RELATIVE_TOLERANCE)
    status = str(result.get("field_status", "")).casefold().replace(" ", "")
    if driven and "persistent" in status:
        return False
    stable_status = any(
        token in status
        for token in ("holding", "persistent", "stable", "standby")
    )
    return stable_status and abs(measured - target_oe) <= tolerance


# CombinedControlApp._read_ppms_cryogen_display of the main program, as a function.
def read_ppms_cryogen_display(ppms):
    """Read only: chamber pressure and LEVEL?; never start a helium fill.

    PPMS channel 19 and LEVEL? follow the PSI/Frappy PPMS driver mapping:
    https://github.com/SampleEnvironment/frappy/blob/master/frappy_psi/ppms.py
    LEVEL? returns percent plus freshness flag; a stale reading is labelled.
    This does not read the separate EverCool helium bottle pressure.
    """
    result = {"pressure_display": "Unknown / not supported by interface", "helium_display": "Unknown / not supported by interface"}
    if ppms.backend != PPMS_QDINSTRUMENT_BACKEND:
        return result
    reader = PPMSMotorDebug(ppms.active)
    try:
        pressure = reader._read_item(19)
        if pressure < 0:
            raise ValueError("Negative pressure")
        result["pressure_display"] = f"{pressure:.5g} Torr"
    except Exception as error:
        result["pressure_display"] = f"Unknown / {short_status_error(error, 35)}"
    try:
        import System
        with reader.controller.lock:
            method = reader._method("SendPPMSCommand", 5)
            args = System.Array[System.Object](5)
            args[0], args[1], args[2] = "LEVEL?", "", ""
            args[3], args[4] = System.Int32(64), System.Double(2.0)
            code = method.Invoke(reader.controller.instrument, args)
            reader._check_return(method, code)
            if str(args[2] or "").strip():
                raise RuntimeError(str(args[2]))
            values = [float(v.strip()) for v in str(args[1]).strip().split(",")]
            if len(values) != 2 or not all(math.isfinite(v) for v in values) or not 0 <= values[0] <= 100 or values[1] not in (0, 1):
                raise ValueError("LEVEL? reply format or range invalid")
            result["helium_display"] = f"{values[0]:.2f}%" + (" (stale)" if values[1] == 0 else "")
            result["helium_percent"] = values[0]
            result["helium_fresh"] = values[1] == 1
    except Exception as error:
        result["helium_display"] = f"Unknown / {short_status_error(error, 35)}"
    return result
# ===== END copied from Transport_all_NH_dev.py =====

# The copied instrument code, as ``core.<name>``: this module itself, so a
# replaced helper (e.g. the echoed VISA manager) is the one the copied
# controllers call.
core = sys.modules[__name__]

HERE = Path(__file__).resolve().parent
OE_PER_T = 10_000.0

# name, PPMS angle (deg), write field (Oe), pulse current (mA), as the
# user's figure (2026-10-09).  0.15 T is 1500 Oe.  At 270 deg the current's
# sign picks A or B (both +1500 Oe); at 360 deg D is +1500 Oe and E -1500 Oe,
# both +22 mA.  D and E are at 360 deg rather than 0 (the same orientation):
# 90 deg from A and B instead of 270 deg.  C and F (user, 2026-10-09) are
# written at 270 deg with no field, by the current alone: C -23 mA, F +23 mA.
DEFAULT_STATES = (
    ("D", 360.0, 1500.0, 22.0),
    ("A", 270.0, 1500.0, -22.0),
    ("B", 270.0, 1500.0, 22.0),
    ("E", 360.0, -1500.0, 22.0),
    ("C", 270.0, 0.0, -23.0),
    ("F", 270.0, 0.0, 23.0),
)
# The manual settings' own fields, the manual pulse width among them (a
# manual pulse never changes the runs' width); decel, field rate, frequency
# and the pulse count and compliance are shared with the Write and Read boxes.
MANUAL_DEFAULTS = {"angle": "360", "field": "0", "current_ma": "0.5", "pulse_ma": "22",
                   "pulse_width_us": "30"}  # As the Write box starts.
DEFAULT_SEQUENCE = "D A B E"
DEFAULT_CYCLE = "D A B E B A"
APP_TITLE = "MS-fourState"
# This program's PPMS address (user, 2026-10-09); the main program's
# addresses stay in the list below it.
PPMS_HOST = "192.168.137.137"
PPMS_HOSTS = (PPMS_HOST, *(host for host in PPMS_HOST_CHOICES if host != PPMS_HOST))
# The window opens maximised, filling the screen (user, 2026-10-09).  This is
# its size when un-maximised, where the screen allows (the lab PC's 2560 x
# 1440 does), else smaller; minimum 1100 x 760.
WINDOW_SIZE = (1720, 1240)
WINDOW_MINIMUM = (1100, 760)


def fill_screen(root):
    """Maximise the window: zoomed on Windows (the lab PC) and macOS,
    -zoomed on X11, else the whole screen's size."""
    try:
        root.state("zoomed")
        return "zoomed"
    except tk.TclError:
        pass
    try:
        root.attributes("-zoomed", True)
        return "-zoomed"
    except tk.TclError:
        pass
    root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")
    return "geometry"
MODES = {"cycle": "Cycle", "sequence": "Sequence"}  # The Mode list, first is the default.
STATE_COLORS = {"D": "#000000", "A": "#c62828", "B": "#1565c0", "E": "#2e7d32",
                "C": "#ef6c00", "F": "#00838f"}
DEFAULTS = {
    "pulse_width_us": "30",
    "pulse_count": "1",
    "compliance_v": "15",
    "field_rate": f"{core.PPMS_FIELD_RATE_DEFAULT_OE_S:g}",
    "approach": "No overshoot",
    "field_settle_s": "2",
    "after_pulse_s": "1",
    "decel": "14",
    "read_current_ma": "0.5",
    "read_frequency_hz": f"{core.PPMS_TRANSPORT_FREQUENCY_HZ:g}",
    "read_time_s": "15",  # The user's figure, 2026-10-09.
    "read_settle_s": "1",
    "repeats": "1",
}
APPROACHES = {"Linear": 0, "No overshoot": 1}
CONNECTIONS = {"ppms": "PPMS (QDInstrument)", "6221": "Keithley 6221", "sr830": "SR830"}
CONSOLE_NAMES = {"ppms": "PPMS", "6221": "6221", "sr830": "SR830"}
CONSOLE_COLORS = {"background": "#11161c", "note": "#e8edf2", "sent": "#7fd4ff",
                  "reply": "#a9b6c2", "error": "#ff7b72", "time": "#6c7a88"}
CONSOLE_LINES = 3000  # Kept on screen; a run's log.txt keeps them all.
# The PPMS status box, as the PPMS page's live readings.  Read only.  Polled
# while idle; during a run or a manual action it shows that action's own
# readings instead, so nothing extra is asked of the instruments.
STATUS_POLL_S = 2.0
CRYOGEN_POLL_S = 30.0      # Chamber pressure and LEVEL? (helium), as the PPMS page.
STATUS_EMIT_S = 1.0        # At most one reading a second from a run to the box.
LAMP_COLORS = {"off": "#9aa0a6", "on": "#2e9d4f", "failed": "#d32f2f"}
ANGLE_TOLERANCE_DEG = 0.06  # As PPMSMotorDebug.move_and_verify.
ROTATOR_AT_REST = ("AtTarget", "AtIndexSwitch")
# Field statuses that end a manual Set H at once, rather than after its margin.
FIELD_FAULTS = ("magnetfailure", "currenterror")
MANUAL_READING_INTERVAL_S = 2.0  # The Now line while a manual Set H waits.
LARGE_ROTATION_DEG = 30.0   # As the PPMS page's manual Move.
FIELD_TIMEOUT_MARGIN_S = 600.0
SHUTDOWN_FIELD_MARGIN_S = 120.0
PULSE_DONE_MARGIN_S = 0.2
READ_SAMPLE_INTERVAL_S = 0.1
READ_OVERLOAD_RETRIES = 3
READ_CURRENT_MAX_RMS_A = 5e-3  # A typing slip such as 70 for 0.7 mA is refused.
LOCKIN_MAX_TAU_S = 10.0        # 5 time constants are waited before each read.
AUTORANGE_MAX_CHANGES = 24
AUTORANGE_LOW_FRACTION = 0.10
# The PPMS page's SR830 baseline (_initialize_ppms_sr830), first harmonic.
SR830_BASELINE = (
    ("FMOD", 0), ("RSLP", core.PPMS_SR830_REFERENCE_SLOPE), ("HARM", 1),
    ("ISRC", 1), ("IGND", 0), ("ICPL", 0), ("ILIN", 0), ("SYNC", 0),
    ("SENS", 9), ("OFLT", 10), ("OFSL", 1), ("RMOD", 1), ("PHAS", 0),
)
SR830_RECORDED = ("HARM", "FMOD", "RSLP", "ISRC", "IGND", "ICPL", "ILIN", "SYNC",
                  "SENS", "OFLT", "OFSL", "RMOD", "PHAS")
CSV_COLUMNS = (
    "step", "cycle", "state", "time_s", "timestamp",
    "angle_set_deg", "angle_read_deg",
    "write_field_set_oe", "write_field_read_oe", "write_field_status",
    "pulse_current_ma", "pulse_width_us", "pulse_count", "compliance_v", "pulse_in_compliance",
    "read_field_oe", "read_field_status", "temperature_k",
    "read_current_ma_rms", "read_frequency_hz",
    "x_v", "y_v", "r_v", "theta_deg", "x_std_v", "y_std_v", "samples",
    "sensitivity_code", "full_scale_v", "rxy_ohm",
)


def base_state(name):
    return str(name).rstrip("'")


@dataclass(frozen=True)
class Settings:
    states: dict
    sequence: tuple
    repeats: object  # int, or None for "until Stop"
    pulse_width_s: float
    pulse_count: int
    compliance_v: float
    field_rate_oe_s: float
    approach_code: int
    field_settle_s: float
    after_pulse_s: float
    decel: int
    read_current_rms_a: float
    read_frequency_hz: float
    read_time_s: float
    read_settle_s: float
    autorange: bool

    @property
    def total_steps(self):
        return None if self.repeats is None else self.repeats * len(self.sequence)

    def iter_steps(self):
        """(cycle, state) in order; endless when repeats is None."""
        cycle = 0
        while self.repeats is None or cycle < self.repeats:
            cycle += 1
            for name in self.sequence:
                yield cycle, name

    def large_rotations(self):
        """Turns over LARGE_ROTATION_DEG per cycle, and between cycles."""
        angles = [self.states[name][0] for name in self.sequence]
        within = sum(abs(b - a) > LARGE_ROTATION_DEG for a, b in zip(angles, angles[1:]))
        wrap = abs(angles[0] - angles[-1]) > LARGE_ROTATION_DEG
        return within, wrap


def parse_sequence(text, names):
    """'D A B E', 'd,a,b,e' or 'D-A-B-E' -> ('D', 'A', 'B', 'E')."""
    lookup = {name.upper(): name for name in names}
    sequence = []
    for token in str(text).replace(",", " ").replace(";", " ").replace("-", " ").split():
        name = lookup.get(token.upper())
        if name is None:
            raise ValueError(f"Unknown state {token!r} in the sequence; use {', '.join(names)}")
        sequence.append(name)
    if not sequence:
        raise ValueError("The sequence is empty; enter states such as D A B E")
    return tuple(sequence)


def _number(text, label):
    try:
        value = float(str(text).strip())
    except ValueError:
        raise ValueError(f"{label}: not a number ({text!r})") from None
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


def _whole(text, label, low, high):
    value = _number(text, label)
    if not value.is_integer() or not low <= value <= high:
        raise ValueError(f"{label} must be a whole number from {low} to {high}")
    return int(value)


def _shared_settings(values):
    """The Write and Read boxes' values, checked: Settings keyword arguments."""
    width_s = _number(values["pulse_width_us"], "Pulse width (us)") / 1e6
    count = _whole(values["pulse_count"], "Pulses per write", 1, 1000)
    compliance = _number(values["compliance_v"], "Compliance (V)")
    rate = _number(values["field_rate"], "Field rate (Oe/s)")
    if values["approach"] not in APPROACHES:
        raise ValueError("Choose a field approach")
    decel = _whole(values["decel"], "Rotator decel", 0, 14)
    # Checked here too, not only per state: a manual action has no states,
    # and Stop's return to 0 Oe needs a rate the PPMS accepts.
    core.validate_ppms_field_command(0.0, rate)
    core.Keithley6221Controller._validate(0.0, width_s, compliance, count)
    read_rms_a = _number(values["read_current_ma"], "Read current (mA RMS)") / 1000.0
    if not 0 < read_rms_a <= READ_CURRENT_MAX_RMS_A:
        raise ValueError(f"Read current must be above 0 and at most {READ_CURRENT_MAX_RMS_A * 1e3:g} mA RMS")
    frequency = _number(values["read_frequency_hz"], "Read frequency (Hz)")
    if not 1.0 <= frequency <= 100_000.0:
        raise ValueError("Read frequency must be 1-100000 Hz")
    settle = {}
    for key, label in (("field_settle_s", "Field settle (s)"), ("after_pulse_s", "Wait after pulse (s)"),
                       ("read_settle_s", "Extra read settle (s)")):
        settle[key] = _number(values[key], label)
        if not 0 <= settle[key] <= 3600:
            raise ValueError(f"{label} must be 0-3600 s")
    read_time = _number(values["read_time_s"], "Read time (s)")
    if not 0.5 <= read_time <= 3600:
        raise ValueError("Read time must be 0.5-3600 s")
    return dict(
        pulse_width_s=width_s, pulse_count=count, compliance_v=compliance,
        field_rate_oe_s=rate, approach_code=APPROACHES[values["approach"]],
        field_settle_s=settle["field_settle_s"], after_pulse_s=settle["after_pulse_s"],
        decel=decel, read_current_rms_a=read_rms_a, read_frequency_hz=frequency,
        read_time_s=read_time, read_settle_s=settle["read_settle_s"],
    )


def build_settings(states, sequence_text, values, autorange=True, until_stop=False):
    """Check every value the run will send, before anything is sent.

    ``states``: {name: (angle text, field Oe text, pulse mA text)};
    ``values``: the DEFAULTS keys as entered.
    """
    shared = _shared_settings(values)
    parsed = {}
    for name, (angle_text, field_text, pulse_text) in states.items():
        angle = _number(angle_text, f"{name} angle")
        field = _number(field_text, f"{name} field")
        pulse_ma = _number(pulse_text, f"{name} pulse current")
        core.PPMSMotorDebug.validate_move(angle, shared["decel"], angle)
        core.validate_ppms_field_command(field, shared["field_rate_oe_s"])
        core.Keithley6221Controller._validate(
            pulse_ma / 1000.0, shared["pulse_width_s"], shared["compliance_v"], shared["pulse_count"])
        parsed[name] = (angle, field, pulse_ma)
    sequence = parse_sequence(sequence_text, tuple(parsed))
    repeats = None if until_stop else _whole(values["repeats"], "Repeats", 1, 100_000)
    return Settings(states=parsed, sequence=sequence, repeats=repeats, autorange=bool(autorange), **shared)


def manual_settings(values, current_ma_text=None):
    """The shared settings for one manual action: no states, nothing to cycle.
    ``current_ma_text`` replaces the read current (the manual I mA RMS)."""
    if current_ma_text is not None:
        values = dict(values, read_current_ma=current_ma_text)
    return Settings(states={}, sequence=(), repeats=1, autorange=False, **_shared_settings(values))


class ManualDeclined(Exception):
    """A manual action the user declined at its confirmation; nothing sent."""


def sr830_query(lockin, command, count):
    """As the PPMS page: three attempts, clearing the bus after a bad reply."""
    last = ""
    error = None
    with lockin.lock:
        if lockin.instrument is None:
            raise RuntimeError("SR830 is not connected")
        for attempt in range(3):
            try:
                last = lockin.instrument.query(command).strip()
                values = tuple(float(part) for part in last.split(","))
                if len(values) != count or not all(math.isfinite(v) for v in values):
                    raise ValueError(f"expected {count} finite values")
                return values
            except Exception as caught:
                error = caught
                if attempt < 2:
                    try:
                        lockin.instrument.clear()
                    except Exception:
                        pass
                    time.sleep(0.08)
    raise RuntimeError(f"SR830 {command!r} kept returning bad replies: {last!r}") from error


def sr830_write(lockin, command):
    """Write, then check *ESR? for a rejected command, as the PPMS page."""
    with lockin.lock:
        if lockin.instrument is None:
            raise RuntimeError("SR830 is not connected")
        lockin.instrument.write(command)
        time.sleep(0.03)
        status = int(round(sr830_query(lockin, "*ESR?", 1)[0]))
        if status & 0x30:
            raise RuntimeError(f"SR830 rejected {command!r}; *ESR?={status}")


def initialize_lockin(lockin):
    """The PPMS page's SR830 baseline at connection, first harmonic."""
    with lockin.lock:
        lockin.instrument.write("*CLS")  # A disconnect can leave *ESR? latched.
        for command, value in SR830_BASELINE:
            sr830_write(lockin, f"{command} {value}")
        for command in ("DDEF 1,1,0", "DDEF 2,1,0"):
            sr830_write(lockin, command)
        for command, expected in SR830_BASELINE:
            actual = sr830_query(lockin, f"{command}?", 1)[0]
            if actual != expected:
                raise RuntimeError(f"SR830 setup readback mismatch: {command}={actual:g}, expected {expected}")


class EchoedResource:
    """A VISA instrument whose every write, query and read is echoed to the
    command console, as a command-line session would show it: "> sent",
    "< reply", "! error".  Everything else is the instrument's own."""

    def __init__(self, resource, name, echo):
        object.__setattr__(self, "_resource", resource)
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_echo", echo)

    def write(self, command, *args, **kwargs):
        self._echo(f"{self._name} > {command}")
        try:
            return self._resource.write(command, *args, **kwargs)
        except Exception as error:
            self._echo(f"{self._name} ! {error}")
            raise

    def query(self, command, *args, **kwargs):
        self._echo(f"{self._name} > {command}")
        try:
            reply = self._resource.query(command, *args, **kwargs)
        except Exception as error:
            self._echo(f"{self._name} ! {error}")
            raise
        self._echo(f"{self._name} < {str(reply).strip()}")
        return reply

    def read(self, *args, **kwargs):
        reply = self._resource.read(*args, **kwargs)
        self._echo(f"{self._name} < {str(reply).strip()}")
        return reply

    def __getattr__(self, name):
        return getattr(self._resource, name)

    def __setattr__(self, name, value):
        setattr(self._resource, name, value)


_ECHO_TARGET = {"echo": lambda line: None, "name_of": str}


def echo_visa_commands(echo, name_of):
    """From now on every instrument the main program's shared VISA manager
    opens is an EchoedResource named ``name_of(resource_name)``, echoing to
    ``echo`` (the latest window's).  The manager itself stays the same
    object, so its reference count is kept."""
    _ECHO_TARGET.update(echo=echo, name_of=name_of)
    acquire = core.acquire_shared_visa_manager
    if getattr(acquire, "echoes", False) is True:
        return

    def acquire_echoed():
        manager = acquire()
        if getattr(manager, "_echoed", False) is not True:
            open_resource = manager.open_resource

            def open_echoed(resource_name, *args, **kwargs):
                return EchoedResource(open_resource(resource_name, *args, **kwargs),
                                      _ECHO_TARGET["name_of"](resource_name),
                                      lambda line: _ECHO_TARGET["echo"](line))

            manager.open_resource = open_echoed
            manager._echoed = True
        return manager

    acquire_echoed.echoes = True
    core.acquire_shared_visa_manager = acquire_echoed


class EchoedPPMS:
    """The PPMS interface with its field commands and readings echoed, as
    EchoedResource does for VISA.  Everything else is the controller's own."""

    APPROACH_NAMES = {code: name for name, code in APPROACHES.items()}

    def __init__(self, ppms, echo):
        object.__setattr__(self, "_ppms", ppms)
        object.__setattr__(self, "_echo", echo)

    @staticmethod
    def describe(data):
        return (f"T {float(data['temperature_k']):.3f} K {data.get('temperature_status', '')} | "
                f"H {float(data['field_oe']):+.1f} Oe {data.get('field_status', '')}")

    def _reply(self, data):
        try:
            self._echo(f"PPMS < {self.describe(data)}")
        except (KeyError, TypeError, ValueError):
            self._echo(f"PPMS < {data}")
        return data

    def connect(self, backend, host, port, *args, **kwargs):
        self._echo(f"PPMS > connect {backend} {host}:{port}")
        try:
            return self._reply(self._ppms.connect(backend, host, port, *args, **kwargs))
        except Exception as error:
            self._echo(f"PPMS ! {error}")
            raise

    def set_field(self, target, rate, approach, mode):
        self._echo(f"PPMS > SetField {float(target):+g} Oe at {float(rate):g} Oe/s, "
                   f"{self.APPROACH_NAMES.get(approach, approach)}, {'Persistent' if mode == 0 else 'Driven'}")
        try:
            return self._reply(self._ppms.set_field(target, rate, approach, mode))
        except Exception as error:
            self._echo(f"PPMS ! {error}")
            raise

    def read_environment(self):
        try:
            return self._reply(self._ppms.read_environment())
        except Exception as error:
            self._echo(f"PPMS ! {error}")
            raise

    def disconnect(self):
        self._echo("PPMS > disconnect")
        return self._ppms.disconnect()

    def __getattr__(self, name):
        return getattr(self._ppms, name)

    def __setattr__(self, name, value):
        setattr(self._ppms, name, value)


def connect_lockin(lockin, resource):
    """Connect, then apply the baseline; disconnect again if it fails."""
    identity = lockin.connect(resource)
    try:
        initialize_lockin(lockin)
    except BaseException:
        try:
            lockin.disconnect()
        except Exception:
            pass
        raise
    return identity


def source_query(source, command):
    with source.lock:
        if source.instrument is None:
            raise RuntimeError("Keithley 6221 is not connected")
        return float(source.instrument.query(command).strip())


def tau_seconds(code):
    if not 0 <= code <= 19:
        raise RuntimeError("SR830 time-constant readback invalid")
    return (1 if code % 2 == 0 else 3) * 10 ** (code // 2 - 5)


class FourStateRun:
    """The measurement itself, without Tk: runs in a worker thread."""

    def __init__(self, ppms, source, lockin, settings, *, emit, stop, motor=None,
                 clock=time.monotonic, sleep=time.sleep):
        self.ppms = ppms
        self.source = source
        self.lockin = lockin
        self.settings = settings
        self.emit = emit
        self.stop = stop
        self.motor = motor if motor is not None else core.PPMSMotorDebug(ppms.active)
        self.clock = clock
        self.sleep = sleep
        self.commanded_field = None  # Last field this run sent, sent or not confirmed.
        self.compliance_latch = True  # STAT:MEAS:EVEN? answered so far.
        self.shutdown_problems = []
        self.lockin_settings = {}
        self.environment_shown = None
        self.t0 = None             # The run's start: the timeline's zero (None: no timeline).
        self.current_state = None  # The state being written or read, for the timeline.

    # -- helpers ---------------------------------------------------------
    def check(self):
        if self.stop.is_set():
            raise core.PPMSScanCancelled("Stopped")

    def wait(self, seconds):
        if self.stop.wait(max(0.0, float(seconds))):
            raise core.PPMSScanCancelled("Stopped")

    def trace(self, quantity, value, state=None):
        """One timeline entry, at the time since the run started: H (Oe, each
        PPMS reading), I (mA RMS, the 6221 level when it changes), pulse (mA,
        each write pulse), X (V, each SR830 sample), read (V, a read's mean)."""
        if self.t0 is not None:
            self.emit("trace", (round(self.clock() - self.t0, 3), quantity, value, state))

    def source_off(self):
        """Output off, confirmed by OUTP? = 0."""
        self.source.output_off()
        if int(round(source_query(self.source, "OUTP?"))) != 0:
            raise RuntimeError("6221 output did not read back off")
        self.trace("I", 0.0)

    def environment(self):
        """The PPMS reading; at most one a second goes to the status box."""
        data = self.ppms.read_environment()
        if self.environment_shown is None or self.clock() - self.environment_shown >= STATUS_EMIT_S:
            self.emit("ppms-reading", dict(data))
            self.environment_shown = self.clock()
        try:
            self.trace("H", float(data["field_oe"]), self.current_state)
        except (KeyError, TypeError, ValueError):
            pass
        return data

    def lockin_settle_s(self):
        return max(0.1, 5.0 * tau_seconds(int(sr830_query(self.lockin, "OFLT?", 1)[0])))

    # -- field and angle ------------------------------------------------
    def command_field(self, target, mode=1):
        """``mode`` 1 Driven (runs), 0 Persistent (the manual Set H)."""
        self.check()
        self.commanded_field = target  # Before sending: it may take effect even if the call fails.
        s = self.settings
        return self.ppms.set_field(target, s.field_rate_oe_s, s.approach_code, mode)

    def field_to(self, target):
        """Driven setpoint; returns the reading once settled for the settle time."""
        s = self.settings
        result = self.command_field(target)
        try:
            distance = abs(target - float(result["field_oe"]))
        except (KeyError, TypeError, ValueError):
            distance = abs(target)
        deadline = self.clock() + distance / s.field_rate_oe_s + FIELD_TIMEOUT_MARGIN_S
        settled_since = None
        while True:
            self.check()
            data = self.environment()
            if core.ppms_field_is_ready(data, target, driven=True):
                settled_since = self.clock() if settled_since is None else settled_since
                if self.clock() - settled_since >= s.field_settle_s:
                    return data
            else:
                settled_since = None
            if self.clock() >= deadline:
                raise RuntimeError(
                    f"Field did not settle at {target:+g} Oe: {data.get('field_oe')} Oe / {data.get('field_status')}")
            self.wait(0.5)

    def ensure_zero_field(self):
        if self.commanded_field in (None, 0.0):
            data = self.environment()
            if core.ppms_field_is_ready(data, 0.0):
                return data  # Already at zero (Driven or Persistent): nothing sent.
        return self.field_to(0.0)

    def rotate_to(self, angle):
        before = self.motor.read()
        if (abs(before["angle_deg"] - angle) <= ANGLE_TOLERANCE_DEG
                and before["status"] in ("AtTarget", "AtIndexSwitch")):
            return before["angle_deg"]
        large = abs(angle - before["angle_deg"]) > LARGE_ROTATION_DEG
        self.check()
        result = self.motor.move_and_verify(
            angle, self.settings.decel, cancelled=self.stop.is_set,
            allow_large=large, timeout=900.0 if large else 90.0)
        self.emit("ppms-reading", {"angle_deg": result["angle_deg"], "angle_status": result.get("status", "AtTarget")})
        return result["angle_deg"]

    # -- 6221 -----------------------------------------------------------
    def compliance_events(self):
        """Compliance latched since the last call (STAT:MEAS:EVEN? bit 3).

        'unknown' if the 6221 does not answer; then it is not asked again.
        """
        if not self.compliance_latch:
            return "unknown"
        try:
            return bool(int(round(source_query(self.source, "STAT:MEAS:EVEN?"))) & 8)
        except Exception as error:
            self.compliance_latch = False
            self.emit("log", f"6221 latched compliance not readable ({error}); pulses are not monitored")
            with self.source.lock:
                for _ in range(10):  # Empty the error queue, or the next checked write fails.
                    try:
                        reply = self.source.instrument.query("SYST:ERR?").strip()
                    except Exception:
                        break
                    if reply.split(",", 1)[0].strip() in ("0", "+0"):
                        break
            return "unknown"

    def pulse(self, pulse_ma):
        """The MOKE-6221 page's unipolar pulse (Keithley6221Controller.set_dc_pulse:
        a 50 % square of period 2 x width with offset I/2 and a finite cycle
        count; ARM, OUTP ON and INIT in that order, each checked), with every
        setting read back before ARM and the latched compliance bit read
        around the train.  What the 6221 outputs between ARM and INIT has
        not been measured: check the pulse with an oscilloscope first."""
        s = self.settings
        if pulse_ma == 0:
            self.source_off()
            return {"in_compliance": "no pulse"}
        current_a, width_s, compliance_v, count = core.Keithley6221Controller._validate(
            pulse_ma / 1000.0, s.pulse_width_s, s.compliance_v, s.pulse_count)
        frequency = 1.0 / (2.0 * width_s)
        amplitude = abs(current_a) / 2.0
        offset = current_a / 2.0
        source = self.source
        self.check()
        with source.lock:
            if source.instrument is None:
                raise RuntimeError("Keithley 6221 is not connected")
            for command in (
                "SOUR:WAVE:ABOR",
                f"CURR:COMP {compliance_v:.12g}",
                "SOUR:WAVE:FUNC SQU",
                "SOUR:WAVE:DCYC 50",
                f"SOUR:WAVE:AMPL {amplitude:.12e}",
                f"SOUR:WAVE:OFFS {offset:.12e}",
                f"SOUR:WAVE:FREQ {frequency:.12g}",
                "SOUR:WAVE:RANG BEST",
                "SOUR:WAVE:PMAR:STAT OFF",
                "SOUR:WAVE:EXTR OFF",
                f"SOUR:WAVE:DUR:CYCL {count}",
            ):
                source._write_checked(command)
            for label, command, expected, rel, absolute in (
                    ("amplitude", "SOUR:WAVE:AMPL?", amplitude, 5e-4, 1e-12),
                    ("offset", "SOUR:WAVE:OFFS?", offset, 5e-4, 1e-12),
                    ("frequency", "SOUR:WAVE:FREQ?", frequency, 5e-4, 1e-6),
                    ("compliance", "CURR:COMP?", compliance_v, 5e-4, 1e-6),
                    ("pulse count", "SOUR:WAVE:DUR:CYCL?", count, 0, 0.5)):
                actual = source_query(source, command)
                if not math.isclose(actual, expected, rel_tol=rel, abs_tol=absolute):
                    raise RuntimeError(f"6221 pulse {label} readback mismatch: requested {expected:g}, read {actual:g}")
            self.compliance_events()  # Clears the latch.
            self.check()
            for command in ("SOUR:WAVE:ARM", "OUTP ON", "SOUR:WAVE:INIT"):
                source._write_checked(command)
        self.trace("pulse", pulse_ma, self.current_state)
        self.wait(2.0 * width_s * count + PULSE_DONE_MARGIN_S)  # On Stop, shutdown aborts the train.
        still_on = int(round(source_query(source, "OUTP?"))) == 1
        in_compliance = self.compliance_events()
        self.source_off()
        if still_on:
            raise RuntimeError("6221 output was still on after the pulse train; it has been turned off")
        if in_compliance is True:
            self.emit("log", f"6221 reached compliance ({compliance_v:g} V) during the {pulse_ma:+g} mA pulse")
        return {"in_compliance": in_compliance}

    def start_read_current(self):
        """The PPMS page's sine: phase marker on, as the SR830 reference."""
        s = self.settings
        peak = math.sqrt(2.0) * s.read_current_rms_a
        source = self.source
        self.check()
        with source.lock:
            if source.instrument is None:
                raise RuntimeError("Keithley 6221 is not connected")
            for command in (
                "SOUR:WAVE:ABOR",
                f"CURR:COMP {s.compliance_v:.12g}",
                "SOUR:WAVE:FUNC SIN",
                f"SOUR:WAVE:FREQ {s.read_frequency_hz:.12g}",
                f"SOUR:WAVE:AMPL {peak:.12e}",
                "SOUR:WAVE:OFFS 0",
                "SOUR:WAVE:RANG BEST",
                "SOUR:WAVE:EXTR OFF",
                "SOUR:WAVE:DUR:CYCL INF",
                f"SOUR:WAVE:PMAR {core.NONLOCAL_MARKER_PHASE_DEG:g}",
                f"SOUR:WAVE:PMAR:OLIN {core.NONLOCAL_MARKER_LINE}",
                "SOUR:WAVE:PMAR:STAT ON",
                "OUTP OFF",
            ):
                source._write_checked(command)
            for label, command, expected, rel, absolute in (
                    ("compliance", "CURR:COMP?", s.compliance_v, 5e-4, 1e-6),
                    ("frequency", "SOUR:WAVE:FREQ?", s.read_frequency_hz, 5e-3, 0.1),
                    ("amplitude", "SOUR:WAVE:AMPL?", peak, 5e-4, 1e-12),
                    ("offset", "SOUR:WAVE:OFFS?", 0.0, 0, 1e-12)):
                actual = source_query(source, command)
                if not math.isclose(actual, expected, rel_tol=rel, abs_tol=absolute):
                    raise RuntimeError(f"6221 {label} readback mismatch: requested {expected:g}, read {actual:g}")
            for command in ("SOUR:WAVE:ABOR", "SOUR:WAVE:ARM", "SOUR:WAVE:INIT"):
                source._write_checked(command)
            if int(round(source_query(source, "OUTP?"))) != 1:
                raise RuntimeError("6221 sine output did not turn on")
            source.output_enabled = True
            source.output_mode = "AC"
            source.current_a = s.read_current_rms_a
        self.trace("I", s.read_current_rms_a * 1e3, self.current_state)

    def check_read_current(self):
        if int(round(source_query(self.source, "OUTP?"))) != 1:
            raise RuntimeError("6221 output turned off during the read")
        if int(round(source_query(self.source, "STAT:MEAS:COND?"))) & 8:
            raise RuntimeError("6221 hit compliance during the read; raise the compliance or check the wiring")

    # -- SR830 ----------------------------------------------------------
    def configure_lockin(self):
        """First harmonic, external reference from the 6221 marker, filters
        off, as at connection; the rest is recorded as found."""
        lockin = self.lockin
        with lockin.lock:
            if lockin.instrument is None:
                raise RuntimeError("SR830 is not connected")
            lockin.instrument.write("*CLS")  # A disconnect can leave *ESR? latched.
        required = {"HARM": 1, "FMOD": 0, "RSLP": core.PPMS_SR830_REFERENCE_SLOPE, "ILIN": 0, "SYNC": 0}
        for command, value in required.items():
            sr830_write(lockin, f"{command} {value}")
        found = {command: sr830_query(lockin, f"{command}?", 1)[0] for command in SR830_RECORDED}
        wrong = {command: found[command] for command, value in required.items() if found[command] != value}
        if wrong:
            raise RuntimeError(f"SR830 setup readback mismatch: {wrong}")
        tau = tau_seconds(int(found["OFLT"]))
        if tau > LOCKIN_MAX_TAU_S:
            raise RuntimeError(f"SR830 time constant {tau:g} s is above {LOCKIN_MAX_TAU_S:g} s; set a shorter one")
        self.lockin_settings = {command: int(value) if float(value).is_integer() else value
                                for command, value in found.items()}
        self.lockin_settings["tau_s"] = tau
        for channel, name in ((1, "X"), (2, "Y")):
            offset, expand = sr830_query(lockin, f"OEXP? {channel}", 2)
            self.lockin_settings[f"offset_{name}_percent"] = offset
            self.lockin_settings[f"expand_{name}"] = int(expand)
        self.emit("lockin", dict(self.lockin_settings))

    def wait_reference_lock(self):
        expected = self.settings.read_frequency_hz
        deadline = self.clock() + core.PPMS_TRANSPORT_REFERENCE_TIMEOUT_S
        while True:
            frequency = sr830_query(self.lockin, "FREQ?", 1)[0]
            status = int(round(sr830_query(self.lockin, "LIAS?", 1)[0]))
            if not status & 8 and math.isclose(frequency, expected, rel_tol=5e-3, abs_tol=0.1):
                return frequency
            if self.clock() >= deadline:
                raise RuntimeError(
                    f"SR830 did not lock to the 6221 reference: {frequency:.6g} Hz, LIAS={status}; "
                    "check the 6221 trigger-link/marker cable to the SR830 reference input")
            self.wait(0.2)

    def clear_overload_latch(self):
        sr830_query(self.lockin, "LIAS?", 1)  # LIAS? reports and clears latched bits.

    def autorange(self):
        """One range up on overload, one down below 10 % of full scale; once
        it has gone up it never comes back down in the same call, so the two
        rules cannot alternate.  Returns the code in use."""
        scales = core.NONLOCAL_SR830_FULL_SCALE_V
        code = int(sr830_query(self.lockin, "SENS?", 1)[0])
        went_up = False
        for _ in range(AUTORANGE_MAX_CHANGES):
            if not 0 <= code < len(scales):
                raise RuntimeError(f"SR830 sensitivity readback invalid: {code}")
            magnitude = abs(sr830_query(self.lockin, "OUTP? 3", 1)[0])
            self.clear_overload_latch()
            status = int(round(sr830_query(self.lockin, "LIAS?", 1)[0]))
            if status & 7:
                if code >= len(scales) - 1:
                    raise RuntimeError("SR830 overload at its largest range; check the input")
                requested = code + 1
                went_up = True
            elif not went_up and code > 0 and magnitude < AUTORANGE_LOW_FRACTION * scales[code]:
                requested = code - 1
            else:
                return code
            sr830_write(self.lockin, f"SENS {requested}")
            code = int(sr830_query(self.lockin, "SENS?", 1)[0])
            if code != requested:
                raise RuntimeError(f"SR830 range readback mismatch: requested {requested}, read {code}")
            self.wait(self.lockin_settle_s())
            self.clear_overload_latch()
        return code

    def acquire(self, duration):
        expected = self.settings.read_frequency_hz
        samples = []
        end = self.clock() + duration
        while True:
            self.check()
            x, y, _r, _theta, frequency = sr830_query(self.lockin, "SNAP? 1,2,3,4,9", 5)
            status = int(round(sr830_query(self.lockin, "LIAS?", 1)[0]))
            self.check_read_current()
            if status & 8 or not math.isclose(frequency, expected, rel_tol=5e-3, abs_tol=0.1):
                raise RuntimeError(f"SR830 lost the 6221 reference during the read: {frequency:.6g} Hz, LIAS={status}")
            if status & 7:
                return samples, True
            samples.append((x, y, frequency))
            self.trace("X", x, self.current_state)
            if self.clock() >= end and len(samples) >= 3:
                return samples, False
            self.wait(READ_SAMPLE_INTERVAL_S)

    def read(self):
        s = self.settings
        self.start_read_current()
        self.wait_reference_lock()
        self.wait(self.lockin_settle_s() + s.read_settle_s)
        for attempt in range(READ_OVERLOAD_RETRIES + 1):
            self.clear_overload_latch()
            code = self.autorange() if s.autorange else int(sr830_query(self.lockin, "SENS?", 1)[0])
            samples, overloaded = self.acquire(s.read_time_s)
            if not overloaded:
                break
            if not s.autorange or attempt == READ_OVERLOAD_RETRIES:
                raise RuntimeError("SR830 overload during the read")
        count = len(samples)
        mean_x = sum(v[0] for v in samples) / count
        mean_y = sum(v[1] for v in samples) / count

        def spread(values, mean):
            return math.sqrt(sum((v - mean) ** 2 for v in values) / (count - 1)) if count > 1 else 0.0

        scales = core.NONLOCAL_SR830_FULL_SCALE_V
        return {
            "x_v": mean_x, "y_v": mean_y, "r_v": math.hypot(mean_x, mean_y),
            # Theta of the mean vector, as the main program reports it.
            "theta_deg": math.degrees(math.atan2(mean_y, mean_x)),
            "x_std_v": spread([v[0] for v in samples], mean_x),
            "y_std_v": spread([v[1] for v in samples], mean_y),
            "samples": count,
            "read_frequency_hz": sum(v[2] for v in samples) / count,
            "sensitivity_code": code,
            "full_scale_v": scales[code] if 0 <= code < len(scales) else math.nan,
        }

    # -- the sequence ---------------------------------------------------
    def run(self):
        s = self.settings
        total = s.total_steps
        started = self.clock()
        self.t0 = started
        done = 0
        try:
            self.source_off()
            self.emit("status", "Checking the SR830: 1st harmonic, external reference")
            self.configure_lockin()
            for index, (cycle, name) in enumerate(s.iter_steps(), 1):
                angle, field, pulse_ma = s.states[name]
                self.current_state = name
                prefix = f"Step {index}{f'/{total}' if total else ''} (cycle {cycle}), state {name}"
                self.source_off()
                self.emit("status", f"{prefix}: field to 0 Oe before rotating")
                self.ensure_zero_field()
                self.emit("status", f"{prefix}: rotating to {angle:g}°")
                angle_read = self.rotate_to(angle)
                self.emit("status", f"{prefix}: write field {field:+g} Oe")
                write = self.field_to(field)
                self.emit("status", f"{prefix}: pulse {pulse_ma:+g} mA, {s.pulse_width_s * 1e6:g} µs × {s.pulse_count}")
                pulse = self.pulse(pulse_ma)
                self.wait(s.after_pulse_s)
                self.emit("status", f"{prefix}: field to 0 Oe for the read")
                read_field = self.field_to(0.0)
                self.emit("status", f"{prefix}: reading the SR830 at {s.read_current_rms_a * 1e3:g} mA RMS")
                sample = self.read()
                self.trace("read", sample["x_v"], name)
                self.source_off()
                row = {
                    "step": index, "cycle": cycle, "state": name,
                    "time_s": round(self.clock() - started, 3),
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "angle_set_deg": angle, "angle_read_deg": angle_read,
                    "write_field_set_oe": field, "write_field_read_oe": write.get("field_oe"),
                    "write_field_status": write.get("field_status"),
                    "pulse_current_ma": pulse_ma, "pulse_width_us": s.pulse_width_s * 1e6,
                    "pulse_count": s.pulse_count, "compliance_v": s.compliance_v,
                    "pulse_in_compliance": pulse.get("in_compliance"),
                    "read_field_oe": read_field.get("field_oe"), "read_field_status": read_field.get("field_status"),
                    "temperature_k": read_field.get("temperature_k"),
                    "read_current_ma_rms": s.read_current_rms_a * 1e3,
                    **sample,
                    "rxy_ohm": sample["x_v"] / s.read_current_rms_a,
                }
                self.emit("point", row)
                done += 1
            self.emit("status", f"Finished: {done} states written and read")
            return done
        finally:
            self.shutdown()

    def shutdown(self):
        """6221 off and 0 Oe (Driven), both confirmed by readback.  Never moves
        the rotator.  Problems are kept in ``shutdown_problems``."""
        problems = []
        if self.source.connected:  # Not connected (a manual action): nothing to turn off.
            try:
                self.source_off()
            except Exception as error:
                problems.append(f"6221 not confirmed off: {error}")
        try:
            if not self.ppms.connected:
                raise LookupError  # Nothing to return to 0 Oe from here.
            s = self.settings
            data = self.environment()
            # A field this run commanded may not show in the reading yet.
            if self.commanded_field not in (None, 0.0) or not core.ppms_field_is_ready(data, 0.0):
                self.emit("status", "Returning the field to 0 Oe…")
                self.commanded_field = 0.0
                result = self.ppms.set_field(0.0, s.field_rate_oe_s, s.approach_code, 1)
                start = max(abs(float(result.get("field_oe", 0.0))), abs(float(data.get("field_oe", 0.0))), 1500.0)
                deadline = self.clock() + start / s.field_rate_oe_s + SHUTDOWN_FIELD_MARGIN_S
                # Held for the settle time (at least 3 s): a reading from before
                # the command could otherwise pass for zero.
                hold = max(s.field_settle_s, 3.0)
                settled_since = None
                while True:
                    self.sleep(0.5)
                    data = self.environment()
                    if core.ppms_field_is_ready(data, 0.0, driven=True):
                        settled_since = self.clock() if settled_since is None else settled_since
                        if self.clock() - settled_since >= hold:
                            break
                    else:
                        settled_since = None
                    if self.clock() >= deadline:
                        problems.append(f"field not confirmed at 0 Oe: {data.get('field_oe')} Oe / "
                                        f"{data.get('field_status')}")
                        break
        except LookupError:
            pass
        except Exception as error:
            problems.append(f"0 Oe not confirmed: {error}")
        self.shutdown_problems = problems
        if problems:
            self.emit("log", "Shutdown: " + "; ".join(problems) + ". Check MultiVu and the 6221.")

    # -- manual settings, as on the PPMS page -----------------------------
    def require_rotator_at_rest(self):
        """Refuse (nothing sent) unless the rotator reads AtTarget, as the PPMS
        page blocks its controls while a move may still be under way.  Its
        reading, or None when the PPMS is not connected here."""
        if not self.ppms.connected:
            return None
        reading = self.motor.read()
        if reading["status"] not in ROTATOR_AT_REST:
            raise RuntimeError(
                f"The rotator is {reading['status']} at {reading['angle_deg']:.2f}°: nothing sent. "
                "Read until it shows AtTarget, or check MultiVu")
        return reading

    def require_zero_field(self):
        data = self.environment()
        if not core.ppms_field_is_ready(data, 0.0):
            raise RuntimeError(
                f"The rotator turns only at zero field; the field is {data.get('field_oe')} Oe / "
                f"{data.get('field_status')}. Zero H first")

    def manual_move(self, angle, confirm_large):
        """Move as a run step does: only at zero field and with the rotator at
        rest (refused, nothing sent, otherwise), then the 6221 off.  A turn
        over LARGE_ROTATION_DEG needs ``confirm_large(from_deg, to_deg)`` to
        return True; the field is read again after the answer."""
        self.require_zero_field()
        before = self.require_rotator_at_rest()
        if abs(angle - before["angle_deg"]) > LARGE_ROTATION_DEG:
            if not confirm_large(before["angle_deg"], angle):
                raise ManualDeclined("Move cancelled; MOVE not sent")
            self.check()
            self.require_zero_field()  # The question may have been open a while.
        if self.source.connected:
            self.source_off()
        return self.rotate_to(angle)

    def manual_field(self, target):
        """The PPMS page's Set H and Zero H: to ``target``, ending Persistent.
        Done by that page's rule (_follow_ppms_persistent_setpoint): Persistent
        at the target once the status has left Persistent, or Persistent at
        the target PPMS_PERSISTENT_CONFIRM_S after the command, so a reading
        from before it cannot pass.  Nothing is resent.  A magnet fault ends it
        at once; the reading goes to the Now line every few seconds."""
        s = self.settings
        self.require_rotator_at_rest()
        result = self.command_field(target, mode=0)
        sent = self.clock()
        try:
            distance = abs(target - float(result["field_oe"]))
        except (KeyError, TypeError, ValueError):
            distance = abs(target)
        deadline = sent + distance / s.field_rate_oe_s + core.PPMS_PERSISTENT_MARGIN_S
        tolerance = max(core.PPMS_FIELD_TOLERANCE_OE, abs(target) * core.PPMS_FIELD_RELATIVE_TOLERANCE)
        left_persistent = False
        shown = None
        while True:
            self.check()
            data = self.environment()
            if shown is None or self.clock() - shown >= MANUAL_READING_INTERVAL_S:
                self.emit("manual-reading", dict(data))
                shown = self.clock()
            status = str(data.get("field_status", "")).strip().lower().replace(" ", "")
            if any(fault in status for fault in FIELD_FAULTS):
                raise RuntimeError(f"PPMS reports {data.get('field_status')} at {data.get('field_oe')} Oe; "
                                   "check MultiVu (not resent)")
            try:
                measured = float(data["field_oe"])
            except (KeyError, TypeError, ValueError):
                measured = math.nan
            at_target = abs(measured - target) <= tolerance
            if status not in ("persistent", "stablepersistent"):
                left_persistent = True  # Ramping, or the switch warming or cooling.
            elif at_target and (left_persistent or self.clock() - sent >= core.PPMS_PERSISTENT_CONFIRM_S):
                return data
            elif left_persistent and not at_target:
                raise RuntimeError(f"Persistent at {measured:+.7g} Oe, not {target:+g} Oe; check MultiVu (not resent)")
            if self.clock() >= deadline:
                raise RuntimeError(f"Not Persistent at {target:+g} Oe in time: {data.get('field_oe')} Oe / "
                                   f"{data.get('field_status')}; check MultiVu (not resent)")
            self.wait(0.5)

    def manual_current(self):
        """The PPMS page's Set I: the read sine (settings.read_current_rms_a,
        phase marker on), left on.  The SR830's lock to it is reported, read
        only: nothing is written to the SR830."""
        self.require_rotator_at_rest()
        self.start_read_current()
        if not self.lockin.connected:
            return {"lock": "SR830 not connected"}
        expected = self.settings.read_frequency_hz
        deadline = self.clock() + core.PPMS_TRANSPORT_REFERENCE_TIMEOUT_S
        try:
            while True:
                frequency = sr830_query(self.lockin, "FREQ?", 1)[0]
                status = int(round(sr830_query(self.lockin, "LIAS?", 1)[0]))
                if not status & 8 and math.isclose(frequency, expected, rel_tol=5e-3, abs_tol=0.1):
                    return {"lock": f"SR830 locked at {frequency:.6g} Hz"}
                if self.clock() >= deadline:
                    return {"lock": f"SR830 not locked ({frequency:.6g} Hz, LIAS={status}); check its reference input"}
                self.wait(0.2)
        except core.PPMSScanCancelled:
            raise
        except Exception as error:
            return {"lock": f"SR830 lock not read: {core.short_status_error(error, 60)}"}

    def manual_pulse(self, pulse_ma):
        """One write pulse train as in a run (the Write settings), 6221 off first."""
        if pulse_ma == 0:
            raise ValueError("Pulse mA is 0: nothing to send")
        self.require_rotator_at_rest()
        self.source_off()
        return self.pulse(pulse_ma)

    def manual_read(self):
        """Angle, field, temperature and 6221 output, as far as connected."""
        reading = {}
        if self.ppms.connected:
            reading.update(self.ppms.read_environment())
            try:
                angle = self.motor.read()
                reading["angle_deg"], reading["angle_status"] = angle["angle_deg"], angle["status"]
            except Exception as error:
                reading["angle_error"] = core.short_status_error(error, 60)
        if self.source.connected:
            reading["source_on"] = int(round(source_query(self.source, "OUTP?"))) == 1
        return reading


def read_instrument_status(ppms, source, cryogen=False, motor=None):
    """What the PPMS status box shows: temperature, field, angle, and, with
    ``cryogen``, chamber pressure and helium level (the PPMS page's own read,
    which never starts a fill); the 6221 output.  Read only."""
    status = {"time": datetime.now().strftime("%H:%M:%S")}
    if ppms.connected:
        status.update(ppms.read_environment())
        try:
            angle = (motor or core.PPMSMotorDebug(ppms.active)).read()
            status["angle_deg"], status["angle_status"] = angle["angle_deg"], angle["status"]
        except Exception as error:
            status["angle_error"] = core.short_status_error(error, 40)
        if cryogen:
            status.update(core.read_ppms_cryogen_display(ppms))
    if source.connected:
        try:
            status["source_on"] = int(round(source_query(source, "OUTP?"))) == 1
        except Exception as error:
            status["source_error"] = core.short_status_error(error, 40)
    return status


def run_manual(run, operation):
    """One manual action: (result, outcome, problems).  ``outcome`` is None,
    or ("declined" | "stopped" | "failed", error).  On Stop the run's shutdown
    (6221 off, 0 Oe Driven, read back); on an error the 6221 is turned off
    and the field and angle are left as they are."""
    try:
        return operation(run), None, []
    except ManualDeclined as error:
        return None, ("declined", error), []
    except core.PPMSScanCancelled as error:
        run.shutdown()
        return None, ("stopped", error), list(run.shutdown_problems)
    except BaseException as error:
        problems = []
        if run.source.connected:
            try:
                run.source_off()
            except Exception as off_error:
                problems.append(f"6221 not confirmed off: {off_error}")
        return None, ("failed", error), problems


def nice_ticks(low, high, count=5):
    if not (math.isfinite(low) and math.isfinite(high)):
        return []
    span = high - low
    # A span lost in rounding (equal values) would give a step that adds nothing.
    if span <= max(abs(low), abs(high), 1e-300) * 1e-9:
        middle = (low + high) / 2.0
        half = max(abs(middle) * 0.1, 1e-12)
        low, high = middle - half, middle + half
    raw = (high - low) / max(1, count)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    first = math.ceil(low / step) * step
    ticks = []
    for index in range(int((high - first) / step + 1e-9) + 1):
        if index > 50:
            break
        value = first + index * step
        ticks.append(0.0 if abs(value) < step * 1e-9 else value)
    return ticks


# The plot's time axis: seconds for a short run, then minutes, then hours.
TIME_UNITS = ((600.0, "s", 1.0), (3 * 3600.0, "min", 60.0), (math.inf, "h", 3600.0))
TRACE_COLUMNS = ("time_s", "quantity", "value", "unit", "state")
# Names Windows refuses, as the PPMS page checks a typed file name.
WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                          *(f"LPT{i}" for i in range(1, 10))}


def valid_file_stem(stem):
    """A file name without a path or characters Windows forbids (the PPMS page's rule)."""
    return bool(stem) and not stem.endswith((".", " ")) and not re.search(r'[<>:"/\\|?*\x00-\x1f]', stem) \
        and stem.split(".")[0].upper() not in WINDOWS_RESERVED_NAMES


def run_file_paths(folder, stem):
    """A run's files, all named after its live CSV, side by side in the chosen
    folder: the CSV as on the PPMS page, and the files only this program
    writes (its settings, the timeline, the log and the plot)."""
    folder = Path(folder)
    return {
        "csv": folder / f"{stem}.csv",
        "settings": folder / f"{stem}_settings.json",
        "trace": folder / f"{stem}_trace.csv",
        "log": folder / f"{stem}_log.txt",
        "plot": folder / f"{stem}_plot.eps",
    }


def append_live_csv(path, row):
    """One row to the live CSV, as the PPMS page writes it: opened, appended,
    flushed and synced to disk each time, so a crash keeps every row."""
    with Path(path).open("a", newline="", encoding="utf-8-sig") as handle:
        csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore").writerow(row)
        handle.flush()
        os.fsync(handle.fileno())
TRACE_UNITS = {"H": "Oe", "I": "mA RMS", "pulse": "mA", "X": "V", "read": "V"}


def time_unit(span_s):
    """(name, seconds per unit) for a time axis spanning ``span_s``."""
    return next((name, factor) for limit, name, factor in TIME_UNITS if span_s < limit)


def thin(points, most):
    """Every n-th point, so at most about ``most`` are drawn (a long run)."""
    step = max(1, len(points) // most)
    return points[::step] if step > 1 else points


class StatePlot:
    """A timing diagram of the run against the time since it started: the
    measured field H, the 6221 current I (each write pulse a spike, the read
    sine its RMS level while on), and the SR830 X samples of each read,
    coloured and lettered by state.  So each period, writing and reading,
    shows at its real time and for its real length."""

    PANELS = ("H (T)", "I (mA)", "SR830 X (µV)")
    # The first seconds of each read's SR830 samples, still settling, are not
    # drawn (user, 2026-10-09); the trace CSV keeps every sample.
    HIDDEN_READ_START_S = 5.0
    LABELLED_READS = 60
    MOST_DRAWN = 4000
    REDRAW_DELAY_MS = 300  # Samples come fast; the plot is redrawn at most this often.

    def __init__(self, canvas):
        self.canvas = canvas
        self.traces = []
        self.pending = None
        canvas.bind("<Configure>", lambda event: self.redraw())

    def clear(self):
        self.traces = []
        self.redraw()

    def add(self, trace):
        """One (time_s, quantity, value, state) from FourStateRun.trace."""
        self.traces.append(trace)
        if self.pending is None:
            try:
                self.pending = self.canvas.after(self.REDRAW_DELAY_MS, self._redraw_now)
            except tk.TclError:
                pass

    def _redraw_now(self):
        self.pending = None
        self.redraw()

    @classmethod
    def shown_samples(cls, x_traces, periods):
        """The X samples drawn, in µV: each read period's from
        HIDDEN_READ_START_S after its first sample on."""
        first = {}
        shown = []
        for at, _, value, state in x_traces:
            period = next((index for index, (start, end) in enumerate(periods) if start <= at <= end), None)
            if period is not None:
                first.setdefault(period, at)
                if at < first[period] + cls.HIDDEN_READ_START_S:
                    continue
            shown.append((at, value * 1e6, state))
        return shown

    def redraw(self):
        canvas = self.canvas
        canvas.delete("all")
        width = max(200, canvas.winfo_width())
        height = max(200, canvas.winfo_height())
        left, right, top, bottom = 90, 20, 14, 40
        gap = 12
        panel_height = (height - top - bottom - 2 * gap) / 3
        last = max((trace[0] for trace in self.traces), default=0.0)
        unit, per_unit = time_unit(last)
        x_low, x_high = 0.0, (last * 1.02 or 60.0) / per_unit
        of = {quantity: [trace for trace in self.traces if trace[1] == quantity]
              for quantity in ("H", "I", "pulse", "X", "read")}

        def x_pixel(seconds):
            return left + (seconds / per_unit - x_low) / (x_high - x_low) * (width - left - right)

        # Each panel's values: H in T, I in mA (levels and pulses, from 0), X in µV.
        h_points = [(at, value / OE_PER_T) for at, _, value, _ in of["H"]]
        levels = [(at, value) for at, _, value, _ in of["I"]]
        pulses = [(at, value, state) for at, _, value, state in of["pulse"]]
        reads = [(at, value * 1e6, state) for at, _, value, state in of["read"]]
        # The read periods (read current on), shaded in every panel: 0.5 mA
        # hardly shows beside the pulses, but its period is the readback's.
        periods, since = [], None
        for at, value in levels:
            if value > 0 and since is None:
                since = at
            elif value <= 0 and since is not None:
                periods.append((since, at))
                since = None
        if since is not None:
            periods.append((since, last))
        samples = self.shown_samples(of["X"], periods)
        ranges = (
            [value for _, value in h_points] + [0.0],
            [value for _, value in levels] + [value for _, value, _ in pulses] + [0.0],
            [value for _, value, _ in samples] or [value for _, value, _ in reads],
        )
        labelled = len(reads) <= self.LABELLED_READS
        for index, label in enumerate(self.PANELS):
            y0 = top + index * (panel_height + gap)
            y1 = y0 + panel_height
            for start, end in periods:
                canvas.create_rectangle(x_pixel(start), y0, max(x_pixel(end), x_pixel(start) + 2), y1,
                                        fill="#e3ecf7", outline="")
            canvas.create_rectangle(left, y0, width - right, y1, outline="#555555")
            canvas.create_text(16, (y0 + y1) / 2, text=label, angle=90, anchor="center", font=("TkDefaultFont", 11))
            if index == 1 and periods:
                canvas.create_text(width - right - 6, y0 + 4, anchor="ne", fill="#46637f",
                                   text=f"shaded: read, {max(v for _, v in levels):g} mA RMS",
                                   font=("TkDefaultFont", 9))
            values = ranges[index]
            if values:
                low, high = min(values), max(values)
                pad = (high - low) * 0.12 or max(abs(high), 1.0) * 0.12
                # Room above the highest X sample for the state letters.
                low, high = low - pad, high + pad * (2.5 if index == 2 and labelled else 1.0)
            else:
                low, high = -1.0, 1.0

            def y_pixel(value, low=low, high=high, y0=y0, y1=y1):
                return y1 - (value - low) / (high - low) * (y1 - y0)

            for tick in nice_ticks(low, high, 4):
                y = y_pixel(tick)
                canvas.create_line(left - 4, y, left, y, fill="#555555")
                canvas.create_text(left - 6, y, text=f"{tick:g}", anchor="e", font=("TkDefaultFont", 10))
            if index == 0 and h_points:
                line = [c for at, value in thin(h_points, self.MOST_DRAWN) for c in (x_pixel(at), y_pixel(value))]
                if len(line) >= 4:
                    canvas.create_line(*line, fill="#37474f", width=2)
                else:
                    canvas.create_oval(line[0] - 3, line[1] - 3, line[0] + 3, line[1] + 3, fill="#37474f")
            elif index == 1:
                # The level held until the next change: a step line from 0.
                step, level = [x_pixel(0.0), y_pixel(0.0)], 0.0
                for at, value in levels:
                    step += [x_pixel(at), y_pixel(level), x_pixel(at), y_pixel(value)]
                    level = value
                if levels:
                    step += [x_pixel(last), y_pixel(level)]
                    canvas.create_line(*step, fill="#37474f", width=2)
                for at, value, state in pulses:  # Microseconds long: drawn as a spike.
                    color = STATE_COLORS.get(base_state(state or ""), "#6a1b9a")
                    x = x_pixel(at)
                    canvas.create_line(x, y_pixel(0.0), x, y_pixel(value), fill=color, width=3)
                    canvas.create_oval(x - 4, y_pixel(value) - 4, x + 4, y_pixel(value) + 4, fill=color, outline=color)
            elif index == 2:
                marker = 2 if len(samples) > 400 else 3
                for at, value, state in thin(samples, self.MOST_DRAWN):
                    color = STATE_COLORS.get(base_state(state or ""), "#6a1b9a")
                    x, y = x_pixel(at), y_pixel(value)
                    canvas.create_oval(x - marker, y - marker, x + marker, y + marker, fill=color, outline=color)
                if labelled:
                    for at, value, state in reads:
                        color = STATE_COLORS.get(base_state(state or ""), "#6a1b9a")
                        canvas.create_text(x_pixel(at), y_pixel(value) - 16, text=state, fill=color,
                                           font=("TkDefaultFont", 11, "bold"))
                if not samples and not reads:
                    canvas.create_text((left + width - right) / 2, (y0 + y1) / 2, text="Waiting for data",
                                       fill="#757575")
        axis_y = height - bottom
        for tick in nice_ticks(x_low, x_high, max(2, min(10, int((width - left - right) / 90)))):
            if x_low <= tick <= x_high:
                x = left + (tick - x_low) / (x_high - x_low) * (width - left - right)
                canvas.create_line(x, axis_y, x, axis_y + 4, fill="#555555")
                canvas.create_text(x, axis_y + 6, text=f"{tick:g}", anchor="n", font=("TkDefaultFont", 10))
        canvas.create_text((left + width - right) / 2, height - 12, text=f"Time since start ({unit})",
                           font=("TkDefaultFont", 11))


class FourStateApp:
    def __init__(self, root, mode="cycle", maximise=True):
        if mode not in MODES:
            raise ValueError(f"Unknown mode {mode!r}")
        self.root = root
        self.initial_mode = mode
        self.title = APP_TITLE
        self.data_directory = HERE / f"{APP_TITLE} data"
        root.title(self.title)
        width = max(WINDOW_MINIMUM[0], min(WINDOW_SIZE[0], root.winfo_screenwidth() - 80))
        height = max(WINDOW_MINIMUM[1], min(WINDOW_SIZE[1], root.winfo_screenheight() - 120))
        root.geometry(f"{width}x{height}")
        root.minsize(*WINDOW_MINIMUM)
        if maximise:
            fill_screen(root)
        self.events = queue.Queue()
        # Every command to the instruments, and every reply, goes to the
        # command console (EchoedResource, EchoedPPMS).
        self.resource_names = {}
        echo_visa_commands(self.echo, lambda resource: self.resource_names.get(resource.strip().upper(), resource))
        self.ppms = EchoedPPMS(core.PPMSRemoteReadbackController(self.log_from_thread), self.echo)
        self.source = core.Keithley6221Controller(self.log_from_thread)
        self.lockin = core.SafeVisaIdentityController(self.log_from_thread, "SR830", "SR830", owner_scope=self.title)
        self.stop_event = threading.Event()
        self.worker = None
        self.run = None
        self.busy = False
        self.rows = []
        # The running run's files (run_file_paths), and the latest run's:
        # the log goes to the first, Save plot and the folder dialogs start
        # from the second.
        self.run_paths = None
        self.last_paths = None
        self.record = None
        # Set after a Move that was stopped or failed (the rotator may still
        # be turning; cleared by a reading at rest), and after a field
        # command that was not confirmed (Move refused until Zero H).
        self.motor_pending = False
        self.field_unconfirmed = None
        self.status_polling = False
        self.last_cryogen = None
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.drain_after = root.after(100, self._drain)
        self.status_after = root.after(int(STATUS_POLL_S * 1000), self._poll_status)

    # -- layout ---------------------------------------------------------
    def _build(self):
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        page = ttk.Frame(root, padding=8)
        page.grid(row=0, column=0, sticky="nsew")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)
        self.inputs = []

        # The setup boxes side by side at their own widths, wrapping to a new
        # line only when the window is too narrow (see _flow): a box as wide
        # as the window held mostly blank.
        flow = ttk.Frame(page)
        flow.grid(row=0, column=0, sticky="ew")
        connections = ttk.LabelFrame(flow, text="Connections", padding=6)
        connections.columnconfigure(9, weight=1)
        dll_default = next((str(p) for p in core.PPMSQDInstrumentReadbackController.candidate_dll_paths()
                            if p.is_file()), "")
        self.host_var = tk.StringVar(value=PPMS_HOST)
        self.port_var = tk.StringVar(value=str(core.PPMS_QDINSTRUMENT_DEFAULT_PORT))
        self.dll_var = tk.StringVar(value=dll_default)
        self.source_resource_var = tk.StringVar(value=core.K6221_DEFAULT_RESOURCE)
        self.lockin_resource_var = tk.StringVar(value=core.PPMS_SR830_RESOURCE_OPTIONS[0])
        self.status_vars = {key: tk.StringVar(value="Not connected") for key in ("ppms", "6221", "sr830")}
        # One row per instrument, each with its own Connect and Disconnect,
        # as on the main program's pages: name, address, buttons, lamp, state.
        self.connection_widgets = {}   # key: (address widgets, Connect, Disconnect, lamp)
        self.connection_failed = {key: False for key in CONNECTIONS}
        for row, (key, label) in enumerate(CONNECTIONS.items()):
            # Right-aligned; the column is as wide as the longest name.
            ttk.Label(connections, text=f"{label}:").grid(
                row=row, column=0, sticky="e", padx=(0, 4), pady=1)
            if key == "ppms":
                host = ttk.Combobox(connections, textvariable=self.host_var, values=PPMS_HOSTS, width=16)
                host.grid(row=row, column=1, sticky="w")
                ttk.Label(connections, text="Port").grid(row=row, column=2, sticky="e", padx=(8, 2))
                port = ttk.Entry(connections, textvariable=self.port_var, width=7)
                port.grid(row=row, column=3, sticky="w")
                ttk.Label(connections, text="DLL").grid(row=row, column=4, sticky="e", padx=(8, 2))
                dll = ttk.Entry(connections, textvariable=self.dll_var, width=26)
                dll.grid(row=row, column=5, sticky="ew")
                address = (host, port, dll)
            elif key == "6221":
                address = (ttk.Entry(connections, textvariable=self.source_resource_var, width=18),)
                address[0].grid(row=row, column=1, sticky="w")
            else:
                address = (ttk.Combobox(connections, textvariable=self.lockin_resource_var,
                                        values=core.PPMS_SR830_RESOURCE_OPTIONS, width=16),)
                address[0].grid(row=row, column=1, sticky="w")
            connect = ttk.Button(connections, text="Connect", width=8,
                                 command=lambda selected=key: self.connect_device(selected))
            connect.grid(row=row, column=6, padx=(10, 1), pady=1)
            disconnect = ttk.Button(connections, text="Disconnect", width=10,
                                    command=lambda selected=key: self.disconnect_device(selected))
            disconnect.grid(row=row, column=7, padx=1, pady=1)
            lamp = tk.Canvas(connections, width=16, height=16, highlightthickness=0,
                             background=root.cget("background"))
            lamp.create_oval(3, 3, 13, 13, fill=LAMP_COLORS["off"], outline="#6f757b", tags="lamp")
            lamp.grid(row=row, column=8, padx=(6, 2))
            state = ttk.Label(connections, textvariable=self.status_vars[key], anchor="w")
            state.grid(row=row, column=9, sticky="ew")
            # A long state (a failure) wraps rather than being cut.
            state.bind("<Configure>", lambda event: event.widget.configure(wraplength=max(120, event.width - 4)))
            self.connection_widgets[key] = (address, connect, disconnect, lamp)
        status = self._build_status(flow)

        self.values = {key: tk.StringVar(value=value) for key, value in DEFAULTS.items()}
        manual = self._build_manual(flow)

        states = ttk.LabelFrame(flow, text="States (1500 Oe = 0.15 T)", padding=6)
        for column, title in enumerate(("State", "φ PPMS (°)", "H write (Oe)", "I pulse (mA)")):
            ttk.Label(states, text=title).grid(row=0, column=column, padx=3, sticky="w")
        self.state_vars = {}
        for row, (name, angle, field, pulse) in enumerate(DEFAULT_STATES, 1):
            ttk.Label(states, text=name, foreground=STATE_COLORS.get(base_state(name), "#000000")).grid(
                row=row, column=0, padx=3, sticky="w")
            variables = tuple(tk.StringVar(value=f"{value:g}") for value in (angle, field, pulse))
            for column, variable in enumerate(variables, 1):
                entry = ttk.Entry(states, textvariable=variable, width=9)
                entry.grid(row=row, column=column, padx=3, pady=1, sticky="w")
                self.inputs.append((entry, "normal"))
            self.state_vars[name] = variables

        # Two columns of four, so the row is no taller than the States table:
        # the pulse on the left, the field and rotator on the right.
        write = ttk.LabelFrame(flow, text="Write", padding=6)
        self._parameter_rows(write, (
            ("Pulse width (µs)", "pulse_width_us"),
            ("Pulses per write", "pulse_count"),
            ("Compliance (V)", "compliance_v"),
            ("Wait after pulse (s)", "after_pulse_s"),
            ("Field rate (Oe/s)", "field_rate"),
            ("Field approach", "approach"),
            ("Field settle (s)", "field_settle_s"),
            ("Decel (0 fast–14 slow)", "decel"),
        ), columns=2)
        read = ttk.LabelFrame(flow, text="Read (H = 0, SR830 1st harmonic)", padding=6)
        self._parameter_rows(read, (
            ("Read current (mA RMS)", "read_current_ma"),
            ("Frequency (Hz)", "read_frequency_hz"),
            ("Read time (s)", "read_time_s"),
            ("Extra settle (s)", "read_settle_s"),
        ))
        self.autorange_var = tk.BooleanVar(value=True)
        autorange = ttk.Checkbutton(read, text="SR830 autorange before each read", variable=self.autorange_var)
        autorange.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.inputs.append((autorange, "normal"))
        self._flow(flow, (connections, status, manual, states, write, read))

        run = ttk.LabelFrame(page, text="Run", padding=6)
        run.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        run.columnconfigure(13, weight=1)
        self.mode_var = tk.StringVar(value=MODES[self.initial_mode])
        ttk.Label(run, text="Mode").grid(row=0, column=0, sticky="w")
        mode = ttk.Combobox(run, textvariable=self.mode_var, values=tuple(MODES.values()),
                            state="readonly", width=10)
        mode.grid(row=0, column=1, sticky="w", padx=(4, 10))
        mode.bind("<<ComboboxSelected>>", lambda event: self._apply_mode())
        self.inputs.append((mode, "readonly"))
        self.sequence_label = ttk.Label(run)
        self.sequence_label.grid(row=0, column=2, sticky="w")
        # Each mode keeps its own states, in the same place on the row.
        self.cycle_var = tk.StringVar(value=DEFAULT_CYCLE)
        self.sequence_var = tk.StringVar(value=DEFAULT_SEQUENCE)
        self.cycle_entry = ttk.Entry(run, textvariable=self.cycle_var, width=20)
        self.sequence_entry = ttk.Entry(run, textvariable=self.sequence_var, width=20)
        for entry in (self.cycle_entry, self.sequence_entry):
            entry.grid(row=0, column=3, sticky="w", padx=(4, 10))
            self.inputs.append((entry, "normal"))
        self.count_label = ttk.Label(run, text="Cycles")
        self.count_label.grid(row=0, column=4, sticky="w")
        self.count_entry = ttk.Entry(run, textvariable=self.values["repeats"], width=6)
        self.count_entry.grid(row=0, column=5, sticky="w", padx=(4, 6))
        # Sequence mode's own number of cycles (user, 2026-10-09): the typed
        # states run that many times, e.g. A B A B twice is A B A B A B A B.
        self.sequence_repeats_var = tk.StringVar(value="1")
        self.sequence_count_entry = ttk.Entry(run, textvariable=self.sequence_repeats_var, width=6)
        self.sequence_count_entry.grid(row=0, column=5, sticky="w", padx=(4, 6))
        self.inputs.append((self.sequence_count_entry, "normal"))
        self.until_stop_var = tk.BooleanVar(value=False)
        self.until_stop_box = ttk.Checkbutton(run, text="Until Stop", variable=self.until_stop_var)
        self.until_stop_box.grid(row=0, column=6, sticky="w", padx=(0, 10))
        self.inputs += [(self.count_entry, "normal"), (self.until_stop_box, "normal")]
        self.start_button = ttk.Button(run, text="Start", command=self.start, width=10)
        self.start_button.grid(row=0, column=7, padx=2)
        self.stop_button = ttk.Button(run, text="Stop", command=self.stop, width=8, state="disabled")
        self.stop_button.grid(row=0, column=8, padx=2)
        # As the PPMS page: the run's rows saved again under a name of choice.
        self.save_csv_button = ttk.Button(run, text="Save CSV as", command=self.save_csv_as, width=12,
                                          state="disabled")
        self.save_csv_button.grid(row=0, column=9, padx=2)
        self.save_plot_button = ttk.Button(run, text="Save plot", command=self.save_plot, width=10)
        self.save_plot_button.grid(row=0, column=10, padx=2)
        self.progress = ttk.Progressbar(run, length=100)
        self.progress.grid(row=0, column=11, padx=(10, 6))
        self.status_var = tk.StringVar(value="Connect, check the settings, then Start")
        ttk.Label(run, textvariable=self.status_var).grid(row=0, column=12, columnspan=2, sticky="ew")
        self._apply_mode(initial=True)
        self.safety_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            run, variable=self.safety_var,
            text=("Sample, wiring, pulse current, compliance, field and rotator clearance confirmed safe; "
                  "no other program is driving the PPMS, 6221 or SR830"),
        ).grid(row=1, column=0, columnspan=14, sticky="w", pady=(4, 0))

        lower = ttk.Frame(page)
        lower.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        lower.columnconfigure(0, weight=1)
        lower.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(lower, background="#ffffff", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.plot = StatePlot(self.canvas)
        side = ttk.Frame(lower)
        side.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        side.columnconfigure(0, weight=1)
        side.rowconfigure(2, weight=1)
        self.last_var = tk.StringVar(value="Last read: --")
        ttk.Label(side, textvariable=self.last_var, justify="left", width=32).grid(row=0, column=0, sticky="nw")
        # The console: status lines, and, with the tick, every command sent to
        # the instruments (">") and every reply ("<"), as on a command line.
        bar = ttk.Frame(side)
        bar.grid(row=1, column=0, sticky="ew", pady=(6, 2))
        self.show_commands_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Instrument commands", variable=self.show_commands_var).pack(side="left")
        ttk.Button(bar, text="Clear", width=6, command=self._clear_console).pack(side="right")
        console = ttk.Frame(side)
        console.grid(row=2, column=0, sticky="nsew")
        console.rowconfigure(0, weight=1)
        console.columnconfigure(0, weight=1)
        self.log = tk.Text(console, width=46, height=12, wrap="word", state="disabled",
                           background=CONSOLE_COLORS["background"], foreground=CONSOLE_COLORS["note"],
                           font="TkFixedFont", relief="flat", padx=4, pady=2)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(console, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)
        for tag in ("sent", "reply", "error", "time"):
            self.log.tag_configure(tag, foreground=CONSOLE_COLORS[tag])
        self.last_command = None
        self._update_connection_controls()

    def _build_status(self, parent):
        """The PPMS status box, as the PPMS page's live readings: temperature,
        field and angle large, each with its status; pressure, helium level
        and the 6221 output below."""
        status = ttk.LabelFrame(parent, text="PPMS status (as on the PPMS page)", padding=6)
        self.status_box_vars = {key: tk.StringVar(value=value) for key, value in (
            ("temperature", "-- K"), ("temperature_status", "not read"),
            ("field", "-- Oe"), ("field_status", "not read"),
            ("angle", "-- °"), ("angle_status", "not read"),
            ("pressure", "--"), ("helium", "--"), ("source", "--"), ("time", "not read yet"))}
        for column, (title, value, state) in enumerate((("Temperature", "temperature", "temperature_status"),
                                                        ("Field", "field", "field_status"),
                                                        ("Angle", "angle", "angle_status"))):
            padx = (0 if column == 0 else 22, 0)
            ttk.Label(status, text=title).grid(row=0, column=column, sticky="w", padx=padx)
            ttk.Label(status, textvariable=self.status_box_vars[value], font=("TkDefaultFont", 15, "bold"),
                      width=11, anchor="w").grid(row=1, column=column, sticky="w", padx=padx)
            ttk.Label(status, textvariable=self.status_box_vars[state], foreground="#5f6670",
                      width=16, anchor="w").grid(row=2, column=column, sticky="w", padx=padx)
        line = ttk.Frame(status)
        line.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        for title, key in (("Pressure", "pressure"), ("He level", "helium"), ("6221", "source"), ("Read", "time")):
            ttk.Label(line, text=f"{title}:", foreground="#5f6670").pack(side="left", padx=(0 if key == "pressure" else 12, 3))
            ttk.Label(line, textvariable=self.status_box_vars[key]).pack(side="left")
        return status

    @staticmethod
    def flow_lines(widths, available, gap):
        """Box indices per line: left to right, a new line when the next box
        does not fit (a box wider than the window gets a line of its own)."""
        lines, line, used = [], [], 0
        for index, width in enumerate(widths):
            need = width if not line else used + gap + width
            if line and need > available:
                lines.append(line)
                line, used = [index], width
            else:
                line.append(index)
                used = need
        if line:
            lines.append(line)
        return lines

    def _flow(self, container, boxes, gap=6):
        """Place ``boxes`` in lines (flow_lines), each line's boxes as tall as
        its tallest and its spare width shared out, so no line ends in a gap.
        Redone on a resize, after a connection changes and on the status
        timer, which catches a box asking for another size (a long state)."""
        def arrange(_event=None):
            try:
                available = container.winfo_width()
                widths = [box.winfo_reqwidth() for box in boxes]
                heights = [box.winfo_reqheight() for box in boxes]
            except tk.TclError:
                return
            if available <= 1:
                return
            layout = (available, tuple(widths), tuple(heights))
            if getattr(container, "_flow_layout", None) == layout:
                return
            container._flow_layout = layout
            y = 0
            for line in self.flow_lines(widths, available, gap):
                natural = sum(widths[i] for i in line)
                spare = max(0, available - natural - gap * (len(line) - 1))
                height = max(heights[i] for i in line)
                x = 0
                for position, index in enumerate(line):
                    width = widths[index] + spare * widths[index] // natural
                    if position == len(line) - 1:
                        width = max(widths[index], available - x)
                    boxes[index].place(x=x, y=y, width=width, height=height)
                    x += width + gap
                y += height + gap
            container.configure(height=max(1, y - gap))

        self._arrange_flow = arrange
        container.bind("<Configure>", arrange, add="+")
        container.after_idle(arrange)

    def _show_status(self, reading):
        """Fill the status box from what a reading holds; the rest stays."""
        vars_ = self.status_box_vars
        if "temperature_k" in reading:
            vars_["temperature"].set(f"{float(reading['temperature_k']):.3f} K")
            vars_["temperature_status"].set(str(reading.get("temperature_status", "")))
        if "field_oe" in reading:
            vars_["field"].set(f"{float(reading['field_oe']):+.1f} Oe")
            vars_["field_status"].set(str(reading.get("field_status", "")))
        if "angle_deg" in reading:
            vars_["angle"].set(f"{float(reading['angle_deg']):.2f}°")
            vars_["angle_status"].set(str(reading.get("angle_status", "")))
        elif "angle_error" in reading:
            vars_["angle_status"].set(f"not read: {reading['angle_error']}")
        def short(text, size=18):
            text = str(text)
            return text if len(text) <= size else text[:size - 1] + "…"

        if "pressure_display" in reading:
            vars_["pressure"].set(short(reading["pressure_display"]))
        if "helium_display" in reading:
            vars_["helium"].set(short(reading["helium_display"]))
        if "source_on" in reading:
            vars_["source"].set("ON" if reading["source_on"] else "OFF")
        elif "source_error" in reading:
            vars_["source"].set(f"not read: {reading['source_error']}")
        vars_["time"].set(reading.get("time") or datetime.now().strftime("%H:%M:%S"))

    def _poll_status(self):
        """Every STATUS_POLL_S while idle: read the status in a worker."""
        try:
            if not self.busy and not self.status_polling and (self.ppms.connected or self.source.connected):
                now = time.monotonic()
                cryogen = self.last_cryogen is None or now - self.last_cryogen >= CRYOGEN_POLL_S
                if cryogen and self.ppms.connected:
                    self.last_cryogen = now
                self.status_polling = True
                threading.Thread(target=self._status_worker, args=(cryogen,), daemon=True).start()
        finally:
            self.__dict__.get("_arrange_flow", lambda: None)()
            try:
                self.status_after = self.root.after(int(STATUS_POLL_S * 1000), self._poll_status)
            except tk.TclError:
                pass

    def _status_worker(self, cryogen):
        try:
            reading = read_instrument_status(self.ppms, self.source, cryogen)
        except Exception as error:
            reading = {"time": f"failed: {core.short_status_error(error, 60)}"}
        if "pressure_display" in reading:
            self.echo(f"PPMS < pressure {reading['pressure_display']}, He {reading.get('helium_display', '--')}")
        self.post("status-box", reading)

    def _build_manual(self, page):
        """The PPMS page's settings rows: angle, field and current, one action
        at a time.  Decel, field rate and frequency are the Write and Read
        boxes' own fields (the same values); the pulse uses the Write box's
        width, count and compliance."""
        manual = ttk.LabelFrame(page, text="Manual settings (as on the PPMS page)", padding=6)
        self.manual_vars = {key: tk.StringVar(value=value) for key, value in MANUAL_DEFAULTS.items()}
        self.manual_buttons = {}

        def field(row, column, label, variable):
            ttk.Label(manual, text=label).grid(row=row, column=column, sticky="e",
                                               padx=(0 if column == 0 else 10, 3), pady=1)
            entry = ttk.Entry(manual, textvariable=variable, width=8)
            entry.grid(row=row, column=column + 1, sticky="w", pady=1)
            self.inputs.append((entry, "normal"))

        def button(row, column, text, command):
            widget = ttk.Button(manual, text=text, command=command, width=8)
            widget.grid(row=row, column=column, padx=(8 if column == 4 else 2, 2), pady=1)
            self.inputs.append((widget, "normal"))
            self.manual_buttons[text] = widget

        field(0, 0, "Angle °", self.manual_vars["angle"])
        field(0, 2, "Decel 0–14", self.values["decel"])
        button(0, 4, "Move", self.manual_move)
        button(0, 5, "Read", self.manual_read)
        field(1, 0, "H Oe", self.manual_vars["field"])
        field(1, 2, "Oe/s", self.values["field_rate"])
        button(1, 4, "Set H", self.manual_set_field)
        button(1, 5, "Zero H", lambda: self.manual_set_field(zero=True))
        field(2, 0, "I mA RMS", self.manual_vars["current_ma"])
        field(2, 2, "f Hz", self.values["read_frequency_hz"])
        button(2, 4, "Set I", self.manual_set_current)
        button(2, 5, "I off", self.manual_current_off)
        field(3, 0, "Pulse mA", self.manual_vars["pulse_ma"])
        field(3, 2, "Width µs", self.manual_vars["pulse_width_us"])
        button(3, 4, "Pulse", self.manual_pulse)
        # Readings go to the PPMS status box.  The note wraps under the rows.
        note = ttk.Label(
            manual, foreground="#8a4b00", wraplength=300,
            text=("H ends Persistent · Move only at 0 Oe, 6221 off first · "
                  "Pulse: count and compliance from Write · Stop: 6221 off, 0 Oe"),
        )
        # Across the box, wrapped to its width (300 px until it has one).
        note.grid(row=4, column=0, columnspan=6, sticky="ew", pady=(4, 0))
        note.bind("<Configure>", lambda event: event.widget.configure(wraplength=max(150, event.width - 2)))
        return manual

    # -- manual settings ------------------------------------------------
    def _manual_check(self, *, ppms=False, source=False, safety=True, moves=False):
        """``moves``: the action drives the rotator, field or 6221 (all but
        I off and Read): refused while a stopped Move may still be turning."""
        if moves and self.motor_pending:
            raise ValueError("The rotator may still be turning after the last Move: "
                             "press Read until it shows AtTarget (or check MultiVu)")
        if ppms:
            if not self.ppms.connected:
                raise ValueError("Connect the PPMS first")
            if self.ppms.backend != core.PPMS_QDINSTRUMENT_BACKEND:
                raise ValueError("The rotator and field need the QDInstrument interface")
        if source and not self.source.connected:
            raise ValueError("Connect the 6221 first")
        if safety and not self.safety_var.get():
            raise ValueError("Tick the safety confirmation (under Run) first")

    def _manual_values(self):
        return {key: variable.get() for key, variable in self.values.items()}

    def manual_move(self):
        if self.busy:
            return
        try:
            self._manual_check(ppms=True, moves=True)
            if self.field_unconfirmed is not None:
                raise ValueError(f"The last field command ({self.field_unconfirmed:+g} Oe) was not confirmed: "
                                 "Zero H first, so the rotator never turns in a field")
            settings = manual_settings(self._manual_values())
            angle = _number(self.manual_vars["angle"].get(), "Angle")
            core.PPMSMotorDebug.validate_move(angle, settings.decel, angle)
        except ValueError as error:
            messagebox.showerror(self.title, str(error), parent=self.root)
            return

        def confirm_large(before, target):
            return self._ask_from_worker(
                f"Rotator {before:.4f}° → {target:g}°: a turn over {LARGE_ROTATION_DEG:g}°.\n"
                "Confirm the mechanical travel and wiring clearance; the rotator has no "
                "software stop (keep MultiVu Motion Control at hand). Continue?")

        self._run_manual(f"Moving the rotator to {angle:g}°", settings,
                         lambda run: run.manual_move(angle, confirm_large),
                         lambda result: f"Rotator at {result:.4f}°", kind="move")

    def manual_set_field(self, zero=False):
        if self.busy:
            return
        try:
            self._manual_check(ppms=True, moves=True)
            settings = manual_settings(self._manual_values())
            target = 0.0 if zero else _number(self.manual_vars["field"].get(), "H")
            core.validate_ppms_field_command(target, settings.field_rate_oe_s)
        except ValueError as error:
            messagebox.showerror(self.title, str(error), parent=self.root)
            return
        if zero:
            self.manual_vars["field"].set("0")
        self._run_manual(f"Field to {target:+g} Oe, then Persistent", settings,
                         lambda run: run.manual_field(target),
                         lambda result: f"Field {target:+g} Oe reached; Persistent confirmed",
                         kind="field", target=target)

    def manual_set_current(self):
        if self.busy:
            return
        try:
            self._manual_check(source=True, moves=True)
            settings = manual_settings(self._manual_values(), self.manual_vars["current_ma"].get())
        except ValueError as error:
            messagebox.showerror(self.title, str(error), parent=self.root)
            return
        rms_ma, frequency = settings.read_current_rms_a * 1e3, settings.read_frequency_hz
        self._run_manual(f"6221 sine {rms_ma:g} mA RMS at {frequency:g} Hz", settings,
                         lambda run: run.manual_current(),
                         lambda result: f"6221 ON, read back: {rms_ma:g} mA RMS at {frequency:g} Hz; "
                                        f"{result['lock']}")

    def manual_current_off(self):
        if self.busy:
            return
        try:
            self._manual_check(source=True, safety=False)
        except ValueError as error:
            messagebox.showerror(self.title, str(error), parent=self.root)
            return
        # The defaults, so a typing slip elsewhere never blocks turning off.
        self._run_manual("6221 off", manual_settings(DEFAULTS), lambda run: run.source_off(),
                         lambda result: "6221 output OFF, read back")

    def manual_pulse(self):
        if self.busy:
            return
        try:
            self._manual_check(source=True, moves=True)
            # The manual width; count and compliance from the Write box.
            settings = manual_settings(dict(self._manual_values(),
                                            pulse_width_us=self.manual_vars["pulse_width_us"].get()))
            pulse_ma = _number(self.manual_vars["pulse_ma"].get(), "Pulse mA")
            if pulse_ma == 0:
                raise ValueError("Pulse mA is 0: nothing to send")
            core.Keithley6221Controller._validate(
                pulse_ma / 1000.0, settings.pulse_width_s, settings.compliance_v, settings.pulse_count)
        except ValueError as error:
            messagebox.showerror(self.title, str(error), parent=self.root)
            return
        width_us, count = settings.pulse_width_s * 1e6, settings.pulse_count
        if not messagebox.askyesno(
                self.title,
                f"Send {count} pulse{'s' if count > 1 else ''} of {pulse_ma:+g} mA, {width_us:g} µs, "
                f"compliance {settings.compliance_v:g} V, at the present field and angle?",
                parent=self.root, default="no"):
            return

        def describe(result):
            text = f"Pulse sent: {pulse_ma:+g} mA, {width_us:g} µs × {count}; 6221 off, read back"
            if result.get("in_compliance") is True:
                text += f"; the 6221 reached compliance ({settings.compliance_v:g} V)"
            return text

        self._run_manual(f"Pulse {pulse_ma:+g} mA", settings, lambda run: run.manual_pulse(pulse_ma), describe)

    def manual_read(self):
        if self.busy:
            return
        if not (self.ppms.connected or self.source.connected):
            messagebox.showerror(self.title, "Connect the PPMS or the 6221 first", parent=self.root)
            return
        self._run_manual("Reading", manual_settings(DEFAULTS), lambda run: None,
                         lambda result: "Read: see Now in Manual settings")

    def _ask_from_worker(self, question):
        """Ask on the Tk thread and wait for the answer; Stop cancels."""
        answered = threading.Event()
        answer = []

        def ask():
            try:
                if not self.stop_event.is_set():  # Stopped before it appeared: not asked.
                    answer.append(messagebox.askyesno(self.title, question, parent=self.root, default="no"))
            finally:
                answered.set()

        self.post("call", ask)
        while not answered.wait(0.1):
            if self.stop_event.is_set():
                raise core.PPMSScanCancelled("Stopped")
        if self.stop_event.is_set():
            raise core.PPMSScanCancelled("Stopped")
        return bool(answer and answer[0])

    def _run_manual(self, label, settings, operation, describe, kind="", target=None):
        """``operation(run)`` in a worker, one action at a time; Stop is on.
        ``kind``: "move" or "field" for the latches; ``target``: the field."""
        self.stop_event.clear()
        self._set_busy(True, running=True)
        self.status_var.set(f"{label}…")
        self._append_log(f"Manual: {label}")
        self._manual_log(f"Manual: {label}")
        run = FourStateRun(self.ppms, self.source, self.lockin, settings, emit=self.post, stop=self.stop_event)

        def read():
            try:
                return run.manual_read()
            except Exception as error:
                return {"error": core.short_status_error(error, 80)}

        def target_():
            result, outcome, problems = run_manual(run, operation)
            reading = read()
            if outcome is None and self.stop_event.is_set():
                # Stop came as the action ended: it still means 6221 off and
                # 0 Oe, as it would have a moment earlier.
                run.shutdown()
                outcome, problems = ("stopped", core.PPMSScanCancelled("Stopped")), list(run.shutdown_problems)
                reading = read()
            self.post("call", lambda: self._manual_finished(
                label, describe, result, outcome, problems, reading, kind, target))

        self.worker = threading.Thread(target=target_, daemon=True)
        self.worker.start()

    def _manual_finished(self, label, describe, result, outcome, problems, reading, kind="", target=None):
        self._set_busy(False)
        self.worker = None
        if "status_box_vars" in self.__dict__:
            self._show_status(reading)
        self._append_log(self.format_reading(reading).replace("\n", "   "))
        state = outcome[0] if outcome is not None else "done"
        # Latches: a Move not completed may leave the rotator turning; a field
        # command not confirmed may still act.  Stop's shutdown confirms 0 Oe.
        if kind == "move" and state in ("stopped", "failed"):
            self.motor_pending = True
        if reading.get("angle_status") in ROTATOR_AT_REST:
            self.motor_pending = False
        if kind == "field" and state == "failed":
            self.field_unconfirmed = target
        elif (kind == "field" and state == "done" and target == 0.0) or (state == "stopped" and not problems):
            self.field_unconfirmed = None
        if state == "done":
            text = describe(result)
            if self.stop_event.is_set():
                text += " (Stop came after it had ended: nothing undone)"
        elif state == "declined":
            text = f"{label}: {outcome[1]}"
        elif state == "stopped":
            text = "Stopped: " + ("6221 off and 0 Oe, read back" if not problems
                                  else "PROBLEM: " + "; ".join(problems))
            if kind == "move":
                text += ". If the rotator was moving, check MultiVu; Read until it shows AtTarget"
        else:
            text = f"{label} failed: {core.short_status_error(outcome[1], 100)}"
        self.status_var.set(text)
        self._append_log(text)
        self._manual_log(text)
        if state == "failed" or problems:
            detail = str(outcome[1]) if state == "failed" else text
            if state == "failed":
                notes = []
                if problems:
                    notes += problems
                elif self.source.connected:
                    notes.append("The 6221 has been turned off (read back).")
                if kind == "field":
                    notes.append(f"The PPMS may still be going to {target:+g} Oe; the command is not resent. "
                                 "Zero H, or MultiVu, to return to 0 Oe. Move is refused until then.")
                else:
                    notes.append("The field and angle are left as they are.")
                detail += "\n\n" + "\n".join(notes)
            messagebox.showerror(self.title, detail, parent=self.root)

    def _manual_log(self, text):
        """Manual actions change the sample outside any run: one line each in
        the data folder's manual_log.txt."""
        try:
            self.data_directory.mkdir(parents=True, exist_ok=True)
            with open(self.data_directory / "manual_log.txt", "a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now().isoformat(timespec='seconds')} {text}\n")
        except OSError:
            pass

    @staticmethod
    def format_reading(reading):
        if "error" in reading:
            return f"Now: not read ({reading['error']})"
        first, second = [], []
        if "angle_deg" in reading:
            first.append(f"φ {float(reading['angle_deg']):.2f}° ({reading.get('angle_status')})")
        elif "angle_error" in reading:
            first.append(f"φ not read ({reading['angle_error']})")
        if "field_oe" in reading:
            first.append(f"H {float(reading['field_oe']):+.1f} Oe {reading.get('field_status', '')}")
        if "temperature_k" in reading:
            second.append(f"T {float(reading['temperature_k']):.3f} K")
        if "source_on" in reading:
            second.append(f"6221 {'ON' if reading['source_on'] else 'OFF'}")
        if not first and not second:
            return "Now: nothing connected to read"
        return "Now: " + "   ".join(first) + ("\n" + "   ".join(second) if second else "")

    def _parameter_rows(self, frame, rows, columns=1):
        per_column = math.ceil(len(rows) / columns)
        for index, (label, key) in enumerate(rows):
            group, row = divmod(index, per_column)
            column = 2 * group
            ttk.Label(frame, text=label).grid(row=row, column=column, sticky="w", pady=1,
                                              padx=(0 if group == 0 else 16, 0))
            if key == "approach":
                widget = ttk.Combobox(frame, textvariable=self.values[key], values=tuple(APPROACHES),
                                      state="readonly", width=13)
                enabled = "readonly"
            else:
                widget = ttk.Entry(frame, textvariable=self.values[key], width=10)
                enabled = "normal"
            widget.grid(row=row, column=column + 1, sticky="w", padx=(6, 0), pady=1)
            self.inputs.append((widget, enabled))

    @property
    def mode(self):
        label = self.mode_var.get()
        return next(key for key, text in MODES.items() if text == label)

    def _apply_mode(self, initial=False):
        """Cycle: its states, the number of cycles and Until Stop.
        Sequence: its states and its own number of cycles (default 1)."""
        cycle = self.mode == "cycle"
        self.sequence_label.configure(text="Cycle" if cycle else "Sequence")
        for widget in (self.cycle_entry, self.count_entry, self.until_stop_box):
            if cycle:
                widget.grid()
            else:
                widget.grid_remove()
        for widget in (self.sequence_entry, self.sequence_count_entry):
            if cycle:
                widget.grid_remove()
            else:
                widget.grid()

    # -- events from threads --------------------------------------------
    def post(self, kind, payload=None):
        self.events.put((kind, payload))

    def log_from_thread(self, message):
        message = str(message)
        if message.startswith("PPMS motor"):
            # The rotator's own commands (SEND: MOVE) and readings
            # (GetPPMSItem, angle): console lines, like every other instrument's.
            text = message[len("PPMS motor"):].lstrip(" :")
            self.echo(f"Rotator {'>' if 'SEND' in text else '<'} {text}")
            return
        self.post("log", message)

    def echo(self, line):
        """One console line from any thread: "<name> > sent", "< reply", "! error"."""
        self.post("command", str(line))

    def _drain(self):
        """Handle queued events; one failing handler never stops the rest."""
        try:
            while True:
                try:
                    kind, payload = self.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle(kind, payload)
                except Exception as error:
                    try:
                        self._append_log(f"Display problem ({kind}): {error}")
                    except Exception:
                        pass
        finally:
            try:
                self.drain_after = self.root.after(100, self._drain)
            except tk.TclError:
                pass

    def _handle(self, kind, payload):
        if kind == "log":
            self._append_log(payload)
        elif kind == "command":
            self._append_command(payload)
        elif kind == "file-log":
            self._append_log(payload, show=False)
        elif kind == "status":
            self.status_var.set(payload)
            self._append_log(payload)
        elif kind == "point":
            self._add_point(payload)
        elif kind == "trace":
            self._add_trace(payload)
        elif kind == "lockin":
            self._record_lockin(payload)
        elif kind == "call":
            payload()
        elif kind in ("manual-reading", "ppms-reading"):
            self._show_status(payload)
        elif kind == "status-box":
            self.status_polling = False
            self._show_status(payload)

    def _append_log(self, text, show=True, tag=None):
        stamp = datetime.now().strftime("%H:%M:%S")
        if show:
            self.last_command = None
            self._console_line(stamp, text, tag)
        self._file_log(stamp, text)

    def _console_line(self, stamp, text, tag=None, replace_from=None):
        log = self.log
        log.configure(state="normal")
        if replace_from is not None:
            log.delete(replace_from, "end-1c")
        start = log.index("end-1c")
        log.insert("end", f"{stamp} ", "time")
        log.insert("end", f"{text}\n", tag or ())
        lines = int(log.index("end-1c").split(".")[0])
        if lines > CONSOLE_LINES:
            log.delete("1.0", f"{lines - CONSOLE_LINES}.0")
            start = None  # Trimmed: a repeat starts a new line.
        log.see("end")
        log.configure(state="disabled")
        return start

    def _append_command(self, line):
        """A console command line.  The same line again (a poll that reads the
        same) updates the count on it ("x3") instead of adding a line."""
        stamp = datetime.now().strftime("%H:%M:%S")
        self._file_log(stamp, line)
        if not self.show_commands_var.get():
            return
        tag = "error" if " ! " in line else "sent" if " > " in line else "reply"
        if self.last_command is not None and self.last_command[0] == line and self.last_command[2] is not None:
            count = self.last_command[1] + 1
            start = self._console_line(stamp, f"{line}  ×{count}", tag, replace_from=self.last_command[2])
            self.last_command = (line, count, start)
        else:
            self.last_command = (line, 1, self._console_line(stamp, line, tag))

    def _clear_console(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.last_command = None

    def _file_log(self, stamp, text):
        if self.run_paths is not None:
            try:
                with open(self.run_paths["log"], "a", encoding="utf-8") as handle:
                    handle.write(f"{stamp} {text}\n")
            except OSError:
                pass

    def _set_busy(self, busy, running=False):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for widget, enabled in self.inputs:
            widget.configure(state="disabled" if busy else enabled)
        self._update_connection_controls()
        self.start_button.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")
        if "save_csv_button" in self.__dict__:
            self.save_csv_button.configure(state="normal" if self.rows and not busy else "disabled")

    def _controller(self, key):
        return {"ppms": self.ppms, "6221": self.source, "sr830": self.lockin}[key]

    def _update_connection_controls(self):
        """Connect while not connected, Disconnect while connected, neither
        while busy; the address is fixed while connected."""
        for key, (address, connect, disconnect, lamp) in self.connection_widgets.items():
            connected = self._controller(key).connected
            connect.configure(state="normal" if not self.busy and not connected else "disabled")
            disconnect.configure(state="normal" if not self.busy and connected else "disabled")
            for widget in address:
                widget.configure(state="disabled" if self.busy or connected else "normal")
            color = "on" if connected else "failed" if self.connection_failed[key] else "off"
            lamp.itemconfigure("lamp", fill=LAMP_COLORS[color])
        if "_arrange_flow" in self.__dict__:
            self.root.after_idle(self._arrange_flow)  # A new state may need more room.

    def _in_thread(self, label, work, done):
        self._set_busy(True)
        self.status_var.set(label)

        def target():
            try:
                result = work()
                error = None
            except BaseException as caught:
                result, error = None, caught
            self.post("call", lambda: self._finished(done, result, error))

        threading.Thread(target=target, daemon=True).start()

    def _finished(self, done, result, error):
        self._set_busy(False)
        done(result, error)

    # -- connections ----------------------------------------------------
    def connect_device(self, key):
        """Connect one instrument; the others are left as they are."""
        if self.busy:
            return
        label = CONNECTIONS[key]
        try:
            if key in ("6221", "sr830"):
                address = (self.source_resource_var if key == "6221" else self.lockin_resource_var).get()
                self.resource_names[address.strip().upper()] = CONSOLE_NAMES[key]
            if key == "ppms":
                port = int(self.port_var.get())
                host, dll = self.host_var.get().strip(), self.dll_var.get().strip()
                connect = lambda: self.ppms.connect(core.PPMS_QDINSTRUMENT_BACKEND, host, port, dll)
            elif key == "6221":
                compliance = _number(self.values["compliance_v"].get(), "6221 compliance (V)")
                core.Keithley6221Controller._validate(0.0, 30e-6, compliance, 1)
                resource = self.source_resource_var.get().strip()
                connect = lambda: self.source.connect(resource, compliance)
            else:
                resource = self.lockin_resource_var.get().strip()
                connect = lambda: connect_lockin(self.lockin, resource)
        except ValueError as error:
            messagebox.showerror(self.title, str(error), parent=self.root)
            return

        def work():
            result = connect()
            if key == "ppms":
                return (f"Connected: {result['temperature_k']:.4g} K, "
                        f"{result['field_oe']:+.6g} Oe / {result['field_status']}")
            if key == "sr830":
                return f"Connected, PPMS page settings: {str(result)[:40]}"
            return f"Connected: {str(result)[:60]}"

        def done(text, error):
            self.connection_failed[key] = error is not None
            if error is not None:
                text = f"Failed: {core.short_status_error(error, 110)}"
                self._append_log(f"{label} connection failed: {error}")
            self.status_vars[key].set(text)
            self.status_var.set(f"{label}: {text}")
            self._update_connection_controls()

        self.status_vars[key].set("Connecting…")
        self._in_thread(f"Connecting {label}…", work, done)

    def disconnect_device(self, key):
        """Disconnect one instrument (the 6221 turns its output off first);
        the field and angle are left as they are."""
        if self.busy:
            return
        label = CONNECTIONS[key]

        def done(_result, error):
            self.connection_failed[key] = False
            if error is not None:
                self.status_vars[key].set(f"Disconnect problem: {core.short_status_error(error, 90)}")
                messagebox.showwarning(self.title, f"{label}: {error}", parent=self.root)
            else:
                self.status_vars[key].set("Not connected")
            self.status_var.set(
                f"{label} disconnected" + (" (output off)" if key == "6221" else "")
                + (" (field and angle unchanged)" if key == "ppms" else "")
                + ("" if error is None else "; check it"))
            self._update_connection_controls()

        self.status_vars[key].set("Disconnecting…")
        self._in_thread(f"Disconnecting {label}…", self._controller(key).disconnect, done)

    # -- run ------------------------------------------------------------
    def current_settings(self):
        states = {name: tuple(variable.get() for variable in variables)
                  for name, variables in self.state_vars.items()}
        values = {key: variable.get() for key, variable in self.values.items()}
        if self.mode == "cycle":
            text, until_stop = self.cycle_var.get(), self.until_stop_var.get()
        else:
            text, until_stop = self.sequence_var.get(), False
            values["repeats"] = self.sequence_repeats_var.get()  # Its own Cycles box.
        return build_settings(states, text, values, self.autorange_var.get(), until_stop=until_stop)

    def start(self):
        if self.busy:
            return
        try:
            if not (self.ppms.connected and self.source.connected and self.lockin.connected):
                raise ValueError("Connect the PPMS, the 6221 and the SR830 first")
            if self.ppms.backend != core.PPMS_QDINSTRUMENT_BACKEND:
                raise ValueError("The rotator needs the QDInstrument interface")
            settings = self.current_settings()
            if not self.safety_var.get():
                raise ValueError("Tick the safety confirmation first")
            if self.motor_pending:
                raise ValueError("The rotator may still be turning after the last Move: "
                                 "press Read (Manual settings) until it shows AtTarget")
        except ValueError as error:
            messagebox.showerror(self.title, str(error), parent=self.root)
            return
        if not messagebox.askyesno(
                self.title, self.summary(settings) + "\n\nContinue to choose the save folder and start?",
                parent=self.root, default="no"):
            return
        # As the PPMS page: a folder, then a file name for the live CSV.
        try:
            paths = self._choose_run_files()
        except OSError as error:
            messagebox.showerror(self.title, f"Could not create the live CSV: {error}", parent=self.root)
            return
        if paths is None:
            return
        try:
            record = asdict(settings)
            record["mode"] = self.mode
            record["until_stop"] = settings.repeats is None
            record["states"] = {name: dict(angle_deg=a, write_field_oe=h, pulse_ma=i)
                                for name, (a, h, i) in settings.states.items()}
            record["instruments"] = {"ppms_host": self.host_var.get(), "6221": self.source.identity,
                                     "sr830": self.lockin.identity}
            record["files"] = {key: path.name for key, path in paths.items()}
            paths["settings"].write_text(json.dumps(record, indent=2), encoding="utf-8")
        except OSError as error:
            messagebox.showerror(self.title, f"Could not create the data files: {error}", parent=self.root)
            return
        self.run_paths = self.last_paths = paths
        self.record = record
        self.csv_failed = False
        self.rows = []
        self.save_csv_button.configure(state="disabled")
        self.plot.clear()
        try:
            self.trace_file = open(paths["trace"], "x", newline="", encoding="utf-8-sig")
            self.trace_writer = csv.writer(self.trace_file)
            self.trace_writer.writerow(TRACE_COLUMNS)
        except OSError as error:
            self.trace_file = self.trace_writer = None
            self._append_log(f"Trace CSV not opened: {error}")
        self.steps_per_cycle = len(settings.sequence)
        self.progress.configure(maximum=settings.total_steps or self.steps_per_cycle, value=0)
        self.stop_event.clear()
        self._set_busy(True, running=True)
        self._append_log(f"Live CSV: {paths['csv']}")
        self.run = FourStateRun(self.ppms, self.source, self.lockin, settings, emit=self.post, stop=self.stop_event)
        run = self.run

        def target():
            try:
                run.run()
                outcome = None
            except core.PPMSScanCancelled as error:
                outcome = ("stopped", error)
            except BaseException as error:
                outcome = ("failed", error)
            self.post("call", lambda: self._run_finished(outcome, list(run.shutdown_problems)))

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _choose_run_files(self):
        """The PPMS page's way: a folder, then a file name (default
        MS-fourState_<mode>_<time>); a name already there gets _01, _02, …,
        so nothing is overwritten.  The live CSV is created with its header.
        Returns the run's files (run_file_paths), or None if cancelled."""
        start = self.last_paths["csv"].parent if self.last_paths else HERE
        folder = filedialog.askdirectory(
            parent=self.root, title=f"Select the folder for the {self.title} live CSV",
            initialdir=str(start))
        if not folder:
            return None
        default_stem = f"{APP_TITLE}_{MODES[self.mode]}_{time.strftime('%Y%m%d_%H%M%S')}"
        while True:
            requested = simpledialog.askstring(
                f"{self.title} file name",
                f"Folder: {folder}\nEnter a file name (.csv may be omitted):\n"
                f"Leave empty for the default name: {default_stem}.csv\n"
                "An existing name gets a number appended, nothing is overwritten.",
                parent=self.root)
            if requested is None:
                return None
            stem = requested.strip()
            if not stem:
                stem = default_stem
                break
            if stem.lower().endswith(".csv"):
                stem = stem[:-4]
            if valid_file_stem(stem):
                break
            messagebox.showerror("Invalid file name", "Enter a valid file name without a path or "
                                 "characters forbidden in Windows file names.", parent=self.root)
        suffix = 0
        while True:
            name = f"{stem}_{suffix:02d}" if suffix else stem
            paths = run_file_paths(folder, name)
            suffix += 1
            if any(path.exists() for path in paths.values()):
                continue
            try:
                with paths["csv"].open("x", newline="", encoding="utf-8-sig") as handle:
                    csv.DictWriter(handle, fieldnames=CSV_COLUMNS).writeheader()
            except FileExistsError:
                continue
            return paths

    def summary(self, s):
        what = "cycle" if self.mode == "cycle" else "sequence"
        if s.repeats is None:
            lines = [f"Run this {what} until Stop?", ""]
        elif s.repeats == 1:
            lines = ["Run this once?", ""]
        else:
            lines = [f"Run this {what} {s.repeats} times?", ""]
        for index, name in enumerate(s.sequence[:12], 1):
            angle, field, pulse = s.states[name]
            lines.append(f"{index:>2}. {name:<2}  φ {angle:g}°,  H {field:+g} Oe,  pulse {pulse:+g} mA")
        if len(s.sequence) > 12:
            lines.append(f"    … {len(s.sequence)} states in all")
        within, wrap = s.large_rotations()
        repeated = s.repeats is None or s.repeats > 1
        turns = within + (1 if wrap and repeated else 0)
        lines += [
            "",
            f"Pulse: {s.pulse_width_s * 1e6:g} µs × {s.pulse_count}, compliance {s.compliance_v:g} V",
            f"Field: {s.field_rate_oe_s:g} Oe/s, Driven, settle {s.field_settle_s:g} s",
            f"Read at 0 Oe: {s.read_current_rms_a * 1e3:g} mA RMS at {s.read_frequency_hz:g} Hz, "
            f"{s.read_time_s:g} s, SR830 1st harmonic" + (", autorange" if s.autorange else ""),
            "",
            f"The rotator turns only at zero field: {turns} large rotation(s) per pass"
            " (plus one at the start if it is not at the first angle). It has no software stop:"
            " keep MultiVu Motion Control at hand.",
            "At the end, on Stop or on an error: 6221 off, field to 0 Oe (Driven), both read back;"
            " the rotator stays.",
        ]
        return "\n".join(lines)

    def _record_lockin(self, settings):
        if self.record is None or self.run_paths is None:
            return
        self.record["sr830_settings"] = settings
        self.run_paths["settings"].write_text(json.dumps(self.record, indent=2), encoding="utf-8")

    def _add_point(self, row):
        self.rows.append(row)
        try:
            if self.run_paths is not None:
                append_live_csv(self.run_paths["csv"], row)
        except OSError as error:
            self._append_log(f"CSV write failed at step {row['step']}: {error}")
            if not self.__dict__.get("csv_failed"):
                self.csv_failed = True
                messagebox.showwarning(self.title, f"Writing the CSV failed: {error}\n\nThe run continues and the "
                                       "plot keeps every point; Save CSV as saves them all at the end.",
                                       parent=self.root)
        finally:
            done = len(self.rows)
            self.progress.configure(value=done if self.run is None or self.run.settings.total_steps
                                    else (done - 1) % self.steps_per_cycle + 1)
            self.last_var.set(
                f"Last read: step {row['step']}, state {row['state']}\n"
                f"X = {row['x_v'] * 1e6:+.4g} µV   Y = {row['y_v'] * 1e6:+.4g} µV\n"
                f"θ = {row['theta_deg']:+.2f}°   R'xy = {row['rxy_ohm'] * 1e3:+.4g} mΩ\n"
                f"H read {row['read_field_oe']:+.4g} Oe, T = {row['temperature_k']:.4g} K")

    def _add_trace(self, trace):
        """The timeline: to the plot, and to the run's trace CSV."""
        self.plot.add(trace)
        writer = self.__dict__.get("trace_writer")
        if writer is not None:
            at, quantity, value, state = trace
            try:
                writer.writerow((at, quantity, f"{value:.9g}", TRACE_UNITS.get(quantity, ""), state or ""))
                self.trace_file.flush()
            except OSError as error:
                self.trace_writer = None
                self._append_log(f"Trace CSV write failed: {error}; the plot keeps the timeline")

    def _run_finished(self, outcome, problems):
        try:
            if self.__dict__.get("trace_file") is not None:
                self.trace_file.close()
        except OSError:
            pass
        self.trace_file = self.trace_writer = None
        try:
            if self.rows:
                self.save_plot(quiet=True)
        finally:
            self._set_busy(False)
            self.worker = None
        count = len(self.rows)
        if self.__dict__.get("csv_failed"):
            problems = problems + ["some CSV rows could not be written"]
        off = "6221 off and 0 Oe, read back" if not problems else "PROBLEM: " + "; ".join(problems)
        if not problems:
            self.field_unconfirmed = None  # The shutdown confirmed 0 Oe.
        if outcome is None:
            text = f"Finished: {count} points; {off}"
        elif outcome[0] == "stopped":
            text = f"Stopped after {count} points; {off}. If the rotator was moving, check MultiVu"
        else:
            text = f"Failed after {count} points: {core.short_status_error(outcome[1], 100)}; {off}"
        self.status_var.set(text)
        self._append_log(text)
        self.run_paths = None
        if outcome is not None and outcome[0] == "failed" or problems:
            detail = str(outcome[1]) if outcome is not None and outcome[0] == "failed" else "Run ended."
            if problems:
                detail += "\n\nShutdown problems:\n" + "\n".join(problems) + "\n\nCheck MultiVu and the 6221."
            messagebox.showerror(self.title, detail, parent=self.root)

    def stop(self):
        if self.worker is not None:
            self.stop_event.set()
            self.status_var.set("Stopping: 6221 off and field to 0 Oe after the current action…")

    def save_csv_as(self):
        """As the PPMS page's Save CSV as: every row of the last run, to a file of choice."""
        if not self.rows:
            return
        start = self.last_paths["csv"].parent if self.last_paths else HERE
        selected = filedialog.asksaveasfilename(
            parent=self.root, title=f"Save {self.title} CSV as", defaultextension=".csv",
            filetypes=(("CSV", "*.csv"),), initialdir=str(start),
            initialfile=time.strftime(f"{APP_TITLE}_{MODES[self.mode]}_%Y%m%d_%H%M%S.csv"))
        if not selected:
            return
        try:
            with open(selected, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(self.rows)
        except OSError as error:
            messagebox.showerror(f"Save {self.title} CSV", str(error), parent=self.root)
            return
        self.status_var.set(f"Saved: {selected}")

    def save_plot(self, quiet=False):
        """At the end of a run, beside its CSV; by the button, to a file of choice."""
        if quiet:
            if not self.last_paths:
                return
            path = self.last_paths["plot"]
        else:
            start = self.last_paths["csv"].parent if self.last_paths else HERE
            name = self.last_paths["plot"].name if self.last_paths else f"{APP_TITLE}_plot.eps"
            selected = filedialog.asksaveasfilename(
                parent=self.root, title=f"Save {self.title} plot as", defaultextension=".eps",
                filetypes=(("EPS", "*.eps"),), initialdir=str(start), initialfile=name)
            if not selected:
                return
            path = Path(selected)
        try:
            self.canvas.update_idletasks()
            self.canvas.postscript(file=str(path), colormode="color")
        except (OSError, tk.TclError) as error:
            if not quiet:
                messagebox.showerror(self.title, f"Could not save the plot: {error}", parent=self.root)
            return
        if not quiet:
            self.status_var.set(f"Plot saved: {path}")

    def close(self):
        if self.worker is not None or self.busy:
            messagebox.showwarning(self.title, "Wait for the run or the connection to finish before closing.",
                                   parent=self.root)
            return
        for controller in (self.source, self.lockin, self.ppms):
            try:
                controller.disconnect()
            except Exception:
                pass
        for after in ("drain_after", "status_after"):
            try:
                self.root.after_cancel(getattr(self, after))
            except (AttributeError, tk.TclError):
                pass
        try:
            self.root.update_idletasks()  # Layout jobs still queued run now, not on a closed window.
        except tk.TclError:
            pass
        self.root.destroy()


def main(mode="cycle"):
    root = tk.Tk()
    FourStateApp(root, mode)
    root.mainloop()


if __name__ == "__main__":
    main()
