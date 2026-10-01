"""Microwave_Transport_Test_NH_dev. Standalone fixed-field two-frequency test.
Instrument controllers copied from integrated development source; no hardware on import.
"""
from __future__ import annotations


import csv


import ctypes


import datetime as datetime_module


import json


import math


import os


import queue


import re


import socket


import subprocess


import sys


import threading


import time


import tkinter as tk


from pathlib import Path


from tkinter import filedialog, font as tkfont, messagebox, ttk


from typing import Callable


def configure_tk_font_system(root: tk.Misc) -> tuple[str, str]:
    """Apply one predictable font system to every Tk/ttk window.

    The Windows control computer is the primary target, so Microsoft YaHei UI
    is preferred there: unlike Segoe UI it contains Chinese glyphs itself and
    does not produce visibly different fallback glyphs inside one label.  The
    selected family is resolved from fonts actually installed on the machine;
    named Tk fonts are then updated so ordinary Tk widgets, ttk widgets,
    dialogs and dynamically-created child windows inherit the same result.

    Serial logs retain one consistent monospaced family because byte columns
    need to stay aligned, while all operator-facing text uses the UI family.
    """

    installed = tuple(tkfont.families(root))
    installed_lookup = {name.casefold(): name for name in installed}

    def choose(candidates: tuple[str, ...], fallback: str) -> str:
        for candidate in candidates:
            actual = installed_lookup.get(candidate.casefold())
            if actual is not None:
                return actual
        return fallback

    default_font = tkfont.nametofont("TkDefaultFont", root=root)
    fixed_font = tkfont.nametofont("TkFixedFont", root=root)
    default_family = str(default_font.actual("family"))
    fixed_family = str(fixed_font.actual("family"))

    if sys.platform.startswith("win"):
        ui_candidates = (
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "DengXian",
            "Segoe UI",
        )
        mono_candidates = ("Cascadia Mono", "Consolas", "Courier New")
    elif sys.platform == "darwin":
        ui_candidates = ("PingFang SC", "Heiti SC", "Helvetica Neue")
        mono_candidates = ("SF Mono", "Menlo", "Monaco")
    else:
        ui_candidates = (
            "Noto Sans CJK SC",
            "Noto Sans SC",
            "WenQuanYi Micro Hei",
            "DejaVu Sans",
        )
        mono_candidates = ("Noto Sans Mono CJK SC", "DejaVu Sans Mono")

    ui_family = choose(ui_candidates, default_family)
    mono_family = choose(mono_candidates, fixed_family)
    top_level = root.winfo_toplevel()
    ui_scale = float(getattr(top_level, "_moke_ui_scale", 1.0))
    normal_size = max(8, min(12, int(round(10 * ui_scale))))
    small_size = max(8, min(11, int(round(9 * ui_scale))))
    named_specs = {
        "TkDefaultFont": (ui_family, normal_size, "normal"),
        "TkTextFont": (ui_family, normal_size, "normal"),
        "TkMenuFont": (ui_family, normal_size, "normal"),
        "TkHeadingFont": (ui_family, normal_size, "bold"),
        "TkCaptionFont": (ui_family, normal_size, "normal"),
        "TkSmallCaptionFont": (ui_family, small_size, "normal"),
        "TkIconFont": (ui_family, normal_size, "normal"),
        "TkTooltipFont": (ui_family, small_size, "normal"),
        "TkFixedFont": (mono_family, small_size, "normal"),
    }
    available_named_fonts = set(tkfont.names(root))
    for font_name, (family, size, weight) in named_specs.items():
        if font_name not in available_named_fonts:
            continue
        tkfont.nametofont(font_name, root=root).configure(
            family=family,
            size=size,
            weight=weight,
        )

    # Tk widgets that do not have a ttk style (Canvas, Text, Button, etc.)
    # now inherit the same named fonts.  Explicit Canvas sizes below use the
    # resolved family while retaining their intended visual hierarchy.
    root.option_add("*Font", "TkDefaultFont", "widgetDefault")
    root.option_add("*Menu.Font", "TkMenuFont", "widgetDefault")
    setattr(top_level, "_moke_ui_font_family", ui_family)
    setattr(top_level, "_moke_mono_font_family", mono_family)
    setattr(top_level, "_moke_ui_font_size", normal_size)
    setattr(top_level, "_moke_ui_small_font_size", small_size)
    return ui_family, mono_family


try:
    import numpy as np
except ImportError:
    np = None
try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None



try:
    import pyvisa
except ImportError:
    pyvisa = None


try:
    import zhinst.core as zi_core
except ImportError:
    zi_core = None


try:
    import zhinst.utils as zi_utils
except ImportError:
    zi_utils = None


def short_status_error(error: BaseException, limit: int = 120) -> str:
    """One-line error summary suitable for a fixed-height Tk status bar."""
    text = " ".join(str(error).replace("\r", "\n").split())
    first_sentence = re.split(r"(?<=[。；;])\s+|\n", text, maxsplit=1)[0]
    if len(first_sentence) <= limit:
        return first_sentence
    return first_sentence[: max(1, limit - 1)].rstrip() + "…"


class LabOneDotNetAdapter:
    """Python-shaped wrapper around the LabOne-version-matched .NET API.

    LabOne 18.12 predates current Python 3.11 wheels.  Its installer does,
    however, include a matching .NET client library.  Loading that library via
    pythonnet keeps all control inside the current Python process while avoiding
    the incompatible modern zhinst-core transport.
    """

    def __init__(self, host: str, port: int, api_level: int) -> None:
        if sys.platform != "win32":
            raise RuntimeError("LabOne .NET API 仅可在 Windows 控制电脑加载")
        try:
            import clr
        except ImportError as error:
            raise RuntimeError(
                "当前 Python 缺少 pythonnet，无法加载 LabOne 自带的 .NET API"
            ) from error

        dll_directory, api_dll = self._find_dotnet_api()
        self.dll_directory = dll_directory
        self.api_dll = api_dll
        self._dll_directory_handle = None
        if hasattr(os, "add_dll_directory"):
            self._dll_directory_handle = os.add_dll_directory(str(dll_directory))
        if str(dll_directory) not in sys.path:
            sys.path.insert(0, str(dll_directory))
        try:
            # The public assembly resolves ziDotNETCore-win64.dll from the same
            # directory.  Loading by absolute path avoids GAC/version ambiguity.
            assembly = clr.AddReference(str(api_dll))
            from System import Activator, Enum
            api_type = assembly.GetType("zhinst.ziDotNET")
            enum_type = assembly.GetType("zhinst.ZIAPIVersion_enum")
            if api_type is None or enum_type is None:
                # Avoid importing ``zhinst`` here: the installed Python package
                # has the same namespace as the CLR assembly.  Reflection is
                # unambiguous even when both APIs coexist in one interpreter.
                exported = list(assembly.GetExportedTypes())
                if api_type is None:
                    api_type = next(
                        (item for item in exported if item.Name == "ziDotNET"),
                        None,
                    )
                if enum_type is None:
                    enum_type = next(
                        (
                            item for item in exported
                            if item.Name == "ZIAPIVersion_enum"
                        ),
                        None,
                    )
            if api_type is None or enum_type is None:
                raise RuntimeError("DLL 中找不到 ziDotNET 或 API level 枚举")
        except BaseException as error:
            self._close_dll_directory()
            raise RuntimeError(
                f"LabOne .NET DLL 加载失败（{api_dll}）：{error}"
            ) from error

        self._daq = Activator.CreateInstance(api_type)
        enum_level = Enum.ToObject(enum_type, int(api_level))
        try:
            # Use the overload that explicitly permits version mismatch.  When
            # the loaded DLL and Data Server both come from LabOne 18.12 they
            # normally match, but the flag also supports a local server update.
            try:
                self._daq.init(str(host), int(port), enum_level, True)
            except TypeError:
                self._daq.init(str(host), int(port), enum_level)
            self._daq.connect()
        except BaseException as error:
            try:
                self._daq.terminate()
            except BaseException:
                pass
            self._close_dll_directory()
            raise RuntimeError(
                f"LabOne .NET API 连接 {host}:{port} 失败：{error}"
            ) from error

    @staticmethod
    def _find_dotnet_api() -> tuple[Path, Path]:
        """Locate the x64/Win32 LabOne .NET assembly matching this Python."""
        is_64_bit = sys.maxsize > 2**32
        suffix = "win64" if is_64_bit else "win32"
        api_filename = f"ziDotNET-{suffix}.dll"
        roots: list[Path] = []
        for environment_name in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            value = os.environ.get(environment_name, "").strip()
            if value:
                roots.append(Path(value))
        # Standard fallbacks are retained because ProgramW6432 may be absent
        # in a terminal launched by a 32-bit parent process.
        roots.extend((Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")))
        candidates: list[Path] = []
        explicit = os.environ.get("MOKE_LABONE_DOTNET_API", "").strip().strip('"')
        if explicit:
            configured = Path(explicit)
            candidates.append(
                configured if configured.suffix.lower() == ".dll"
                else configured / api_filename
            )
        runtime_directories = {
            Path(__file__).resolve().parent,
            Path(sys.executable).resolve().parent,
        }
        candidates.extend(directory / api_filename for directory in runtime_directories)
        for root in roots:
            candidates.extend(
                (
                    root / "Zurich Instruments/LabOne/API/DotNet" / api_filename,
                    root / "Zurich Instruments/LabOne/API/DotNET" / api_filename,
                    root / "Zurich Instruments/LabOne/API/Dotnet" / api_filename,
                )
            )
        checked: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate).casefold()
            if key in seen:
                continue
            seen.add(key)
            checked.append(str(candidate))
            try:
                if candidate.is_file():
                    core_dll = candidate.with_name(
                        f"ziDotNETCore-{suffix}.dll"
                    )
                    if not core_dll.is_file():
                        raise RuntimeError(
                            f"找到 {candidate.name}，但同目录缺少 {core_dll.name}"
                        )
                    return candidate.parent, candidate
            except OSError:
                continue
        raise RuntimeError(
            "未找到 LabOne 自带的匹配版 .NET API。请确认已安装完整的 "
            "LabOne（包含 API/DotNet）。已检查：" + "；".join(checked)
        )

    def _close_dll_directory(self) -> None:
        handle = self._dll_directory_handle
        self._dll_directory_handle = None
        if handle is not None:
            try:
                handle.close()
            except BaseException:
                pass

    def setInt(self, path: str, value: int) -> None:
        self._daq.setInt(str(path), int(value))

    def getInt(self, path: str) -> int:
        return int(self._daq.getInt(str(path)))

    def sync(self) -> None:
        self._daq.sync()

    def getSample(self, path: str) -> dict[str, float]:
        sample = self._daq.getDemodSample(str(path))
        return {
            "x": float(sample.x),
            "y": float(sample.y),
            "frequency": float(sample.frequency),
            "phase": float(sample.phase),
        }

    def getString(self, path: str) -> str:
        return str(self._daq.getString(str(path)))

    def connectDevice(self, device: str, interface: str) -> None:
        self._daq.connectDevice(str(device), str(interface), "")

    def disconnect(self) -> None:
        try:
            self._daq.disconnect()
        finally:
            self._close_dll_directory()


class LabOneCAPIAdapter:
    """Current-Python adapter for the LabOne-version-matched native C API."""

    class _DemodSample(ctypes.Structure):
        _fields_ = (
            ("timeStamp", ctypes.c_uint64),
            ("x", ctypes.c_double),
            ("y", ctypes.c_double),
            ("frequency", ctypes.c_double),
            ("phase", ctypes.c_double),
            ("dioBits", ctypes.c_uint32),
            ("trigger", ctypes.c_uint32),
            ("auxIn0", ctypes.c_double),
            ("auxIn1", ctypes.c_double),
        )

    def __init__(self, host: str, port: int, api_level: int) -> None:
        if sys.platform != "win32":
            raise RuntimeError("LabOne C API 仅可在 Windows 控制电脑加载")
        self.api_dll = self._find_c_api()
        self._dll_directory_handle = None
        if hasattr(os, "add_dll_directory"):
            self._dll_directory_handle = os.add_dll_directory(
                str(self.api_dll.parent)
            )
        try:
            self._lib = ctypes.WinDLL(str(self.api_dll))
            self._bind_functions()
            self._connection = ctypes.c_void_p()
            self._check(
                self._lib.ziAPIInit(ctypes.byref(self._connection)),
                "ziAPIInit",
            )
            self._check(
                self._lib.ziAPIConnectEx(
                    self._connection,
                    str(host).encode("ascii"),
                    int(port),
                    int(api_level),
                    b"allowVersionMismatch=1",
                ),
                f"ziAPIConnectEx({host}:{port})",
            )
        except BaseException:
            self._destroy()
            raise

    def _bind_functions(self) -> None:
        connection = ctypes.c_void_p
        result = ctypes.c_int
        self._lib.ziAPIInit.argtypes = [ctypes.POINTER(connection)]
        self._lib.ziAPIInit.restype = result
        self._lib.ziAPIConnectEx.argtypes = [
            connection,
            ctypes.c_char_p,
            ctypes.c_uint16,
            ctypes.c_int,
            ctypes.c_char_p,
        ]
        self._lib.ziAPIConnectEx.restype = result
        self._lib.ziAPIDisconnect.argtypes = [connection]
        self._lib.ziAPIDisconnect.restype = result
        self._lib.ziAPIDestroy.argtypes = [connection]
        self._lib.ziAPIDestroy.restype = result
        self._lib.ziAPIConnectDevice.argtypes = [
            connection,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ]
        self._lib.ziAPIConnectDevice.restype = result
        self._lib.ziAPISetValueI.argtypes = [
            connection,
            ctypes.c_char_p,
            ctypes.c_int64,
        ]
        self._lib.ziAPISetValueI.restype = result
        self._lib.ziAPIGetValueI.argtypes = [
            connection,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_int64),
        ]
        self._lib.ziAPIGetValueI.restype = result
        self._lib.ziAPISync.argtypes = [connection]
        self._lib.ziAPISync.restype = result
        self._lib.ziAPIGetDemodSample.argtypes = [
            connection,
            ctypes.c_char_p,
            ctypes.POINTER(self._DemodSample),
        ]
        self._lib.ziAPIGetDemodSample.restype = result

    @staticmethod
    def _candidate_labview_roots() -> list[Path]:
        roots = []
        for base in (r"C:\Program Files", r"C:\Program Files (x86)"):
            ni_root = Path(base) / "National Instruments"
            try:
                roots.extend(
                    item / "instr.lib/Zurich Instruments LabOne"
                    for item in ni_root.glob("LabVIEW*")
                    if item.is_dir()
                )
            except OSError:
                continue
        return roots

    @classmethod
    def _find_c_api(cls) -> Path:
        suffix = "win64" if sys.maxsize > 2**32 else "win32"
        filename = f"ziAPI-{suffix}.dll"
        explicit = os.environ.get("MOKE_LABONE_C_API", "").strip().strip('"')
        candidates: list[Path] = []
        if explicit:
            configured = Path(explicit)
            candidates.append(
                configured if configured.suffix.lower() == ".dll"
                else configured / filename
            )
        for base in (r"C:\Program Files", r"C:\Program Files (x86)"):
            labone = Path(base) / "Zurich Instruments/LabOne"
            candidates.extend(
                (
                    labone / "API/C/lib" / filename,
                    labone / "API/C/bin" / filename,
                    labone / filename,
                )
            )
            try:
                candidates.extend(labone.rglob(filename))
            except OSError:
                pass
        runtime_directories = {
            Path(__file__).resolve().parent,
            Path(sys.executable).resolve().parent,
        }
        candidates.extend(directory / filename for directory in runtime_directories)
        for root in cls._candidate_labview_roots():
            try:
                candidates.extend(root.rglob(filename))
            except OSError:
                continue
        # Some old LabVIEW installations place vendor DLLs outside instr.lib.
        # Perform one bounded recursive search of the NI trees as a final
        # automatic discovery step rather than making the user guess a path.
        for base in (r"C:\Program Files", r"C:\Program Files (x86)"):
            ni_root = Path(base) / "National Instruments"
            try:
                candidates.extend(ni_root.rglob(filename))
            except OSError:
                continue
        checked: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate).casefold()
            if key in seen:
                continue
            seen.add(key)
            checked.append(str(candidate))
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        raise RuntimeError(
            f"未找到匹配位数的 {filename}。请安装 LabOne C API，或将环境变量 "
            f"MOKE_LABONE_C_API 指向该 DLL。已检查：{'；'.join(checked)}"
        )

    @staticmethod
    def _path(path: str) -> bytes:
        return str(path).encode("ascii")

    @staticmethod
    def _check(code: int, operation: str) -> None:
        if int(code) != 0:
            raise RuntimeError(f"{operation} 失败，LabOne C API code={int(code)}")

    def setInt(self, path: str, value: int) -> None:
        self._check(
            self._lib.ziAPISetValueI(
                self._connection, self._path(path), int(value)
            ),
            f"setInt({path})",
        )

    def getInt(self, path: str) -> int:
        value = ctypes.c_int64()
        self._check(
            self._lib.ziAPIGetValueI(
                self._connection, self._path(path), ctypes.byref(value)
            ),
            f"getInt({path})",
        )
        return int(value.value)

    def sync(self) -> None:
        self._check(self._lib.ziAPISync(self._connection), "ziAPISync")

    def getSample(self, path: str) -> dict[str, float]:
        sample = self._DemodSample()
        self._check(
            self._lib.ziAPIGetDemodSample(
                self._connection, self._path(path), ctypes.byref(sample)
            ),
            f"getDemodSample({path})",
        )
        return {
            "x": float(sample.x),
            "y": float(sample.y),
            "frequency": float(sample.frequency),
            "phase": float(sample.phase),
        }

    def getString(self, path: str) -> str:
        # Version text is optional in the GUI identity and is intentionally not
        # needed for measurement.  Avoid a version-specific string ABI here.
        return ""

    def connectDevice(self, device: str, interface: str) -> None:
        self._check(
            self._lib.ziAPIConnectDevice(
                self._connection,
                str(device).encode("ascii"),
                str(interface).encode("ascii"),
                b"",
            ),
            f"connectDevice({device}, {interface})",
        )

    def _destroy(self) -> None:
        connection = getattr(self, "_connection", None)
        library = getattr(self, "_lib", None)
        if connection is not None and connection.value and library is not None:
            try:
                library.ziAPIDestroy(connection)
            except BaseException:
                pass
            connection.value = None
        handle = getattr(self, "_dll_directory_handle", None)
        self._dll_directory_handle = None
        if handle is not None:
            try:
                handle.close()
            except BaseException:
                pass

    def disconnect(self) -> None:
        try:
            if self._connection.value:
                self._lib.ziAPIDisconnect(self._connection)
        finally:
            self._destroy()


NONLOCAL_MFLI_SERVER_HOST = "192.168.69.78"


NONLOCAL_MFLI_SERVER_PORT = 8004


NONLOCAL_MFLI_SERVER_CANDIDATES = (
    # Prefer the PC-side Data Server when LabOne is installed and running.
    # It is normally the best version match for the Python API and can attach
    # DEV4435 through the RNDIS link.  The embedded MFLI server remains the
    # deterministic fallback when no local Data Server is listening.
    ("127.0.0.1", NONLOCAL_MFLI_SERVER_PORT),
    (NONLOCAL_MFLI_SERVER_HOST, NONLOCAL_MFLI_SERVER_PORT),
)


NONLOCAL_MFLI_API_LEVEL = 5


NONLOCAL_MFLI_DEMODULATOR = 0


NONLOCAL_MFLI_EXTREF_DEMODULATOR = 1


NONLOCAL_MFLI_EXTREF_CHANNEL = 0


NONLOCAL_MFLI_INPUT_SCALING = 1.0


NONLOCAL_MFLI_REFERENCE_INPUTS = {
    "Aux Input 1": 8,
    "Aux Input 2": 9,
    "Trigger In 1": 2,
    "Trigger In 2": 3,
}


NONLOCAL_MFLI_DEFAULT_REFERENCE_INPUT = "Aux Input 1"


NONLOCAL_MFLI_LEGACY_PYTHON_ENV = "MOKE_MFLI_PYTHON"


NONLOCAL_SAMPLE_HZ = 1.0


NONLOCAL_LOCKIN_TIME_CONSTANT_S = 1.0


NONLOCAL_LOCKIN_FILTER_ORDER = 2


NONLOCAL_SR830_FULL_SCALE_V = (
    2e-9, 5e-9, 10e-9, 20e-9, 50e-9, 100e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6, 200e-6,
    500e-6, 1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3,
    200e-3, 500e-3, 1.0,
)


STFMR_LOCKIN_DEFAULT_RESOURCES = {
    "kepco": "GPIB0::6::INSTR",
    "gauss": "GPIB0::15::INSTR",
    "smb": "GPIB0::28::INSTR",
    "830": "GPIB0::9::INSTR",
    "mfli": "dev4435",
}


STFMR_KEPCO_MAX_CURRENT_A = 10.0


STFMR_KEPCO_MAX_VOLTAGE_V = 20.0


STFMR_LOCKIN_SR830_SENSITIVITY_CODE = 13  # 50 uV full scale


STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE = 10  # 1 s


STFMR_LOCKIN_SR830_REFERENCE_INPUTS = {
    "Sine": 0,
    "TTL Rising": 1,
    "TTL Falling": 2,
}


STFMR_LOCKIN_SR830_DEFAULT_REFERENCE_INPUT = "Sine"


_INSTRUMENT_LEASE_LOCK = threading.RLock()


_INSTRUMENT_LEASES: dict[str, tuple[object, str]] = {}


_SHARED_VISA_MANAGER_LOCK = threading.RLock()


_SHARED_VISA_MANAGER = None


_SHARED_VISA_MANAGER_USERS = 0


def acquire_shared_visa_manager():
    global _SHARED_VISA_MANAGER, _SHARED_VISA_MANAGER_USERS
    if pyvisa is None:
        raise RuntimeError(
            "缺少 pyvisa；请运行：py -m pip install pyvisa pyvisa-py"
        )
    with _SHARED_VISA_MANAGER_LOCK:
        manager = _SHARED_VISA_MANAGER
        if manager is not None:
            try:
                manager.session
            except BaseException as error:
                if _SHARED_VISA_MANAGER_USERS:
                    raise RuntimeError(
                        "共享 VISA 管理器会话意外失效；请关闭仍显示已连接的"
                        "仪器后重新连接"
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


def acquire_instrument_lease(
    resource: str, owner: object, owner_label: str
) -> str:
    key = str(resource).strip().upper()
    if not key:
        raise ValueError("仪器资源地址不能为空")
    with _INSTRUMENT_LEASE_LOCK:
        current = _INSTRUMENT_LEASES.get(key)
        if current is not None:
            raise RuntimeError(
                f"{resource} 已由“{current[1]}”连接；"
                f"请先在该模块断开，再由“{owner_label}”连接"
            )
        _INSTRUMENT_LEASES[key] = (owner, str(owner_label))
    return key


def release_instrument_lease(resource: str, owner: object) -> None:
    key = str(resource).strip().upper()
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
                "缺少 pyvisa；请运行：py -m pip install pyvisa pyvisa-py"
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
                        f"{self.label} 身份查询 *IDN? 失败：{error}；"
                        "本次未向设备发送任何输出/设定命令，已关闭 VISA 会话"
                    ) from error
                if self.identity_token not in identity.upper():
                    raise RuntimeError(
                        f"{resource_name} 返回的设备不是 {self.label}：{identity}"
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
                                f"{self.label} 连接安全命令 {command!r} 失败：{error}"
                            ) from error
                self.resource_manager = manager
                self.instrument = instrument
                self.resource_name = resource_name
                self.identity = identity
                self.logger(
                    f"{self.owner_scope} {self.label} 已连接：{identity}"
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
                    f"{self.label} 关闭输出失败：" + "; ".join(errors)
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
            self.logger(f"{self.owner_scope} {self.label} 已断开")
            if shutdown_error is not None:
                raise shutdown_error


class STFMRKepco4886Controller:
    """Trace-backed, write-only KEPCO BIT 4886 / BOP 20-10 session.

    The supplied LabVIEW NI I/O Trace never queries this controller.  Opening
    and closing therefore only manage the VISA session; output writes happen
    only after the operator explicitly presses Apply or Zero/OFF.
    """

    def __init__(self, logger: Callable[[str], None]) -> None:
        self.logger = logger
        self.resource_manager = None
        self.instrument = None
        self.resource_name = ""
        self.identity = ""
        self.lock = threading.RLock()
        self.active_mode = ""
        self.output_owned = False
        self.cached_current_a = 0.0
        self.cached_voltage_v = 0.0

    @property
    def connected(self) -> bool:
        return self.instrument is not None

    def connect(self, resource_name: str) -> str:
        if pyvisa is None:
            raise RuntimeError("缺少 pyvisa；请运行：py -m pip install pyvisa")
        resource_name = str(resource_name).strip()
        if not resource_name:
            raise ValueError("KEPCO VISA 资源地址不能为空")
        with self.lock:
            if self.instrument is not None:
                return self.identity
            acquire_instrument_lease(
                resource_name, self, "STFMR / KEPCO BOP 4886 20-10"
            )
            manager = None
            instrument = None
            try:
                manager = acquire_shared_visa_manager()
                instrument = manager.open_resource(resource_name)
                instrument.timeout = 3000
                # LabVIEW capture: exact ASCII buffer, GPIB EOI, no CR/LF.
                instrument.write_termination = None
                instrument.read_termination = None
                instrument.send_end = True
                self.resource_manager = manager
                self.instrument = instrument
                self.resource_name = resource_name
                self.identity = "KEPCO BOP 4886 20-10（未查询）"
                self.logger(
                    "STFMR KEPCO VISA 会话已打开；未发送查询或输出命令"
                )
                return self.identity
            except BaseException:
                if instrument is not None:
                    try:
                        instrument.close()
                    except BaseException:
                        pass
                release_shared_visa_manager(manager)
                release_instrument_lease(resource_name, self)
                raise

    def _write_exact(self, command: str) -> None:
        if self.instrument is None:
            raise RuntimeError("KEPCO 尚未连接")
        payload = str(command).encode("ascii")
        writer = getattr(self.instrument, "write_raw", None)
        if callable(writer):
            writer(payload)
        else:
            self.instrument.write(str(command))

    def _cached_state(self) -> dict[str, object]:
        return {
            "mode": self.active_mode or "CURR",
            "current_a": self.cached_current_a,
            "voltage_v": self.cached_voltage_v,
            "output": self.output_owned,
            "programmed_current_a": self.cached_current_a,
            "programmed_voltage_v": self.cached_voltage_v,
        }

    def readback(self) -> dict[str, object]:
        # Deliberately cached: this BIT 4886 path is write-only in the proven
        # LabVIEW trace.  The Lake Shore 475 is the independent field readback.
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("KEPCO 尚未连接")
            return self._cached_state()

    def set_output(
        self, mode: str, target: float, complementary_limit: float
    ) -> dict[str, object]:
        selected = str(mode).strip().upper()
        target = float(target)
        limit = abs(float(complementary_limit))
        if not math.isfinite(target) or not math.isfinite(limit):
            raise ValueError("KEPCO 目标值和限值必须是有限数")
        if selected == "CURR":
            if abs(target) > STFMR_KEPCO_MAX_CURRENT_A:
                raise ValueError("BOP 20-10 电流目标必须在 ±10 A 内")
            if not 0.0 < limit <= STFMR_KEPCO_MAX_VOLTAGE_V:
                raise ValueError("电流模式的电压限值必须在 0–20 V 内")
        elif selected == "VOLT":
            if abs(target) > STFMR_KEPCO_MAX_VOLTAGE_V:
                raise ValueError("BOP 20-10 电压目标必须在 ±20 V 内")
            if not 0.0 < limit <= STFMR_KEPCO_MAX_CURRENT_A:
                raise ValueError("电压模式的电流限值必须在 0–10 A 内")
        else:
            raise ValueError(f"不支持的 KEPCO 模式：{mode}")

        with self.lock:
            if self.instrument is None:
                raise RuntimeError("KEPCO 尚未连接")
            commands = [f"FUNC:MODE {selected}"]
            if selected == "CURR":
                commands.extend(
                    (
                        f"CURR 0;VOLT {limit:.12g}",
                        "VOLT:RANG:AUTO 1;CURR:RANG:AUTO 1",
                        "OUTP ON",
                        f"CURR {target:.6f}",
                    )
                )
            else:
                commands.extend(
                    (
                        f"VOLT 0;CURR {limit:.12g}",
                        "VOLT:RANG:AUTO 1;CURR:RANG:AUTO 1",
                        "OUTP ON",
                        f"VOLT {target:.6f}",
                    )
                )
            self.active_mode = selected
            try:
                for command in commands:
                    self._write_exact(command)
                    if command == "OUTP ON":
                        # A later setpoint write can fail after the supply has
                        # already been enabled; retain ownership immediately so
                        # the exception path can make a best-effort Zero/OFF.
                        self.output_owned = True
            except BaseException:
                if self.output_owned:
                    try:
                        self.safe_output_off()
                    except BaseException:
                        pass
                raise
            if selected == "CURR":
                self.cached_current_a = target
                self.cached_voltage_v = limit
            else:
                self.cached_voltage_v = target
                self.cached_current_a = limit
            return self._cached_state()

    def set_scan_current(self, current_a: float) -> dict[str, object]:
        """Change only the active current setpoint during an FMR scan.

        The full mode/range/output sequence is sent once at scan start.  Each
        later point is then the single command present in the LabVIEW trace,
        avoiding unnecessary output toggles and GPIB traffic on the old 4886.
        """
        current = float(current_a)
        if not math.isfinite(current):
            raise ValueError("KEPCO 扫描电流必须是有限数")
        if abs(current) > STFMR_KEPCO_MAX_CURRENT_A:
            raise ValueError("BOP 20-10 扫描电流必须在 ±10 A 内")
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("KEPCO 尚未连接")
            if not self.output_owned or self.active_mode != "CURR":
                raise RuntimeError("KEPCO 尚未处于本程序启用的电流输出模式")
            self._write_exact(f"CURR {current:.6f}")
            self.cached_current_a = current
            return self._cached_state()

    def safe_output_off(self) -> None:
        with self.lock:
            if self.instrument is None or not self.output_owned:
                return
            zero = (
                "VOLT 0.000000"
                if self.active_mode == "VOLT"
                else "CURR 0.000000"
            )
            self._write_exact(zero)
            self._write_exact("OUTP OFF")
            self.output_owned = False
            self.cached_current_a = 0.0
            self.cached_voltage_v = 0.0

    def zero_output(self) -> dict[str, object]:
        self.safe_output_off()
        return self._cached_state()

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
                    instrument.close()
                except BaseException:
                    pass
            release_shared_visa_manager(manager)
            self.instrument = None
            self.resource_manager = None
            self.resource_name = ""
            self.identity = ""
            self.active_mode = ""
            release_instrument_lease(resource_name, self)
            self.logger("STFMR KEPCO VISA 会话已关闭")
            if shutdown_error is not None:
                raise shutdown_error


class STFMRGauss475Controller(SafeVisaIdentityController):
    """Read-only Lake Shore 475 field interface preserving front-panel units."""

    UNIT_LABELS = {1: "G", 2: "T", 3: "Oe", 4: "A/m"}

    def __init__(self, logger: Callable[[str], None]) -> None:
        super().__init__(
            logger,
            "Lake Shore 475 高斯计",
            "MODEL475",
            owner_scope="STFMR",
        )

    def read_field(self) -> dict[str, object]:
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("Lake Shore 475 尚未连接")
            value = float(self.instrument.query("RDGFIELD?"))
            unit_code = int(float(self.instrument.query("UNIT?")))
            if not math.isfinite(value):
                raise RuntimeError("Lake Shore 475 回读包含非有限数值")
            return {
                "field": value,
                "unit_code": unit_code,
                "unit": self.UNIT_LABELS.get(unit_code, f"UNIT {unit_code}"),
            }

    @staticmethod
    def value_in_gauss(result: dict[str, object]) -> float:
        """Convert the 475 front-panel unit to gauss for one plot axis."""
        value = float(result["field"])
        unit_code = int(result["unit_code"])
        factors = {
            1: 1.0,                 # G
            2: 1.0e4,               # T -> G
            3: 1.0,                 # Oe -> G in air/vacuum
            4: 4.0 * math.pi / 1000.0,  # A/m -> G in air/vacuum
        }
        if unit_code not in factors:
            raise RuntimeError(f"无法把 Lake Shore UNIT {unit_code} 换算为 G")
        return value * factors[unit_code]


class STFMRSR830Controller(SafeVisaIdentityController):
    """SR830 voltage-input reader for the SMB100A-modulated STFMR path."""

    def __init__(self, logger: Callable[[str], None]) -> None:
        super().__init__(
            logger,
            "Stanford SR830",
            "SR830",
            owner_scope="STFMR",
        )
        self.reference_input = STFMR_LOCKIN_SR830_DEFAULT_REFERENCE_INPUT
        self.sensitivity_index = STFMR_LOCKIN_SR830_SENSITIVITY_CODE
        self.auto_range_enabled = True
        self.low_range_count = 0
        self.range_cooldown = 0

    @staticmethod
    def _numbers(response: str, expected: int) -> list[float]:
        values = [float(item.strip()) for item in str(response).split(",")]
        if len(values) != expected or not all(math.isfinite(v) for v in values):
            raise RuntimeError(f"SR830 回读无法解析：{response!r}")
        return values

    def configure(
        self,
        reference_input: str = STFMR_LOCKIN_SR830_DEFAULT_REFERENCE_INPUT,
        auto_range: bool = True,
    ) -> dict[str, object]:
        reference_input = str(reference_input).strip()
        if reference_input not in STFMR_LOCKIN_SR830_REFERENCE_INPUTS:
            choices = "、".join(STFMR_LOCKIN_SR830_REFERENCE_INPUTS)
            raise ValueError(f"SR830 REF IN 必须选择：{choices}")
        reference_slope = STFMR_LOCKIN_SR830_REFERENCE_INPUTS[reference_input]
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("SR830 尚未连接")
            for command in (
                "*CLS",
                "FMOD 0",       # external reference
                f"RSLP {reference_slope}",
                "HARM 1",
                "ISRC 0",       # voltage input A
                "IGND 0",
                "ICPL 0",
                "ILIN 1",
                f"SENS {STFMR_LOCKIN_SR830_SENSITIVITY_CODE}",
                f"OFLT {STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE}",
                "OFSL 1",
                "RMOD 1",
                "DDEF 1,1,0",
                "DDEF 2,1,0",
            ):
                self.instrument.write(command)
            actual_reference_mode = int(round(float(
                self.instrument.query("FMOD?").strip()
            )))
            actual_reference_slope = int(round(float(
                self.instrument.query("RSLP?").strip()
            )))
            actual_harmonic = int(round(float(
                self.instrument.query("HARM?").strip()
            )))
            actual_sensitivity = int(round(float(
                self.instrument.query("SENS?").strip()
            )))
            if (
                actual_reference_mode != 0
                or actual_reference_slope != reference_slope
                or actual_harmonic != 1
            ):
                raise RuntimeError(
                    "SR830 REF IN 配置回读不符："
                    f"FMOD={actual_reference_mode}, "
                    f"RSLP={actual_reference_slope}, HARM={actual_harmonic}"
                )
            if actual_sensitivity != STFMR_LOCKIN_SR830_SENSITIVITY_CODE:
                raise RuntimeError(
                    "SR830 初始量程回读不符："
                    f"请求 SENS {STFMR_LOCKIN_SR830_SENSITIVITY_CODE}，"
                    f"回读 {actual_sensitivity}"
                )
            self.reference_input = reference_input
            self.sensitivity_index = actual_sensitivity
            self.auto_range_enabled = bool(auto_range)
            self.low_range_count = 0
            self.range_cooldown = 0
            return {
                "reference_input": reference_input,
                "reference_slope": actual_reference_slope,
                "sensitivity_index": actual_sensitivity,
                "auto_range": self.auto_range_enabled,
            }

    def wait_for_reference(
        self,
        expected_frequency_hz: float,
        timeout_s: float = 5.0,
    ) -> dict[str, object]:
        expected = float(expected_frequency_hz)
        if not math.isfinite(expected) or expected <= 0:
            raise ValueError("SR830 预期参考频率必须是有限正数")
        deadline = time.monotonic() + max(float(timeout_s), 0.1)
        frequency = math.nan
        status = 8
        while time.monotonic() < deadline:
            with self.lock:
                if self.instrument is None:
                    raise RuntimeError("SR830 尚未连接")
                frequency = float(self.instrument.query("FREQ?").strip())
                status = int(round(float(
                    self.instrument.query("LIAS?").strip()
                )))
            if not (status & 8) and math.isclose(
                frequency,
                expected,
                rel_tol=5e-3,
                abs_tol=0.1,
            ):
                return {
                    "frequency_hz": frequency,
                    "status": status,
                    "reference_input": self.reference_input,
                }
            time.sleep(0.1)
        raise RuntimeError(
            f"SR830 REF IN 未锁定：已选择 {self.reference_input}，"
            f"期望 {expected:g} Hz，回读 {frequency:g} Hz，LIAS={status}；"
            "请确认 SMB100A LFO OUTPUT 已接到 SR830 REF IN，"
            "并选择与波形匹配的 Sine/TTL 边沿"
        )

    def _auto_range_locked(self, r_value: float, status: int) -> str:
        if not self.auto_range_enabled:
            return ""
        old = int(self.sensitivity_index)
        if not 0 <= old < len(NONLOCAL_SR830_FULL_SCALE_V):
            raise RuntimeError(f"SR830 当前 SENS 索引无效：{old}")
        utilization = abs(float(r_value)) / NONLOCAL_SR830_FULL_SCALE_V[old]
        requested = old
        reason = ""
        if status & 0x07 or utilization >= 0.85:
            requested = min(old + 1, len(NONLOCAL_SR830_FULL_SCALE_V) - 1)
            reason = "overload/high"
            self.low_range_count = 0
        elif self.range_cooldown > 0:
            self.range_cooldown -= 1
            self.low_range_count = 0
        elif utilization < 0.15 and old > 0:
            self.low_range_count += 1
            if self.low_range_count >= 3:
                requested = old - 1
                reason = "low"
                self.low_range_count = 0
        else:
            self.low_range_count = 0
        if requested == old:
            return ""
        self.instrument.write(f"SENS {requested}")
        actual = int(round(float(
            self.instrument.query("SENS?").strip()
        )))
        if actual != requested:
            raise RuntimeError(
                f"SR830 自动量程回读不一致：请求 {requested}，回读 {actual}"
            )
        self.sensitivity_index = actual
        self.range_cooldown = 3
        return f"SENS {old}->{actual} ({reason})"

    def auto_phase(self) -> float:
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("SR830 尚未连接")
            self.instrument.write("APHS")
            time.sleep(0.25)
            return float(self.instrument.query("PHAS?").strip())

    def read_sample(self, expected_frequency_hz: float) -> dict[str, object]:
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("SR830 尚未连接")
            x, y, r_value, theta, frequency = self._numbers(
                self.instrument.query("SNAP? 1,2,3,4,9"), 5
            )
            lias = int(round(float(self.instrument.query("LIAS?").strip())))
            sensitivity_used = int(self.sensitivity_index)
            auto_range_action = self._auto_range_locked(r_value, lias)
        reference_valid = not (lias & 8) and math.isclose(
            frequency,
            float(expected_frequency_hz),
            rel_tol=5e-3,
            abs_tol=0.1,
        )
        return {
            "x_v": x,
            "y_v": y,
            "r_v": r_value,
            "phase_deg": theta,
            "reference_frequency_hz": frequency,
            "reference_valid": reference_valid,
            "status": lias,
            "sensitivity_index": sensitivity_used,
            "sensitivity_full_scale_v": NONLOCAL_SR830_FULL_SCALE_V[
                sensitivity_used
            ],
            "auto_range": auto_range_action,
            "measurement_valid": bool(
                reference_valid and not (lias & 0x07) and not auto_range_action
            ),
        }


class STFMRSMB100AController(SafeVisaIdentityController):
    """Trace-backed SMB100A microwave plus low-frequency AM source."""

    def __init__(self, logger: Callable[[str], None]) -> None:
        super().__init__(
            logger,
            "R&S SMB100A",
            "SMB100A",
            safe_off_commands=("AM:STAT OFF", "LFO OFF", "OUTPUT OFF"),
            owner_scope="STFMR",
        )

    def configure(self, frequency_ghz: float, power_dbm: float, lfo_hz: float) -> None:
        frequency_ghz = float(frequency_ghz)
        power_dbm = float(power_dbm)
        lfo_hz = float(lfo_hz)
        if not all(math.isfinite(v) for v in (frequency_ghz, power_dbm, lfo_hz)):
            raise ValueError("SMB100A 频率和功率必须是有限数")
        if frequency_ghz <= 0.0 or lfo_hz <= 0.0:
            raise ValueError("SMB100A 微波频率和低频调制频率必须大于 0")
        if power_dbm > 25.0:
            raise ValueError("SMB100A 功率软件上限为 25 dBm")
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("SMB100A 尚未连接")
            # Exact setup order recovered from the supplied full LabVIEW trace.
            for command in (
                ":AM:DEPT:LIN 1.0E+2",
                "LFO:VOLT 0.50 V",
                f"LFO:FREQ {lfo_hz:.7g} Hz",
                f":POW {power_dbm:.6g}",
                f"FREQ {frequency_ghz:.9g}GHz",
                "LFO ON",
                "OUTPUT ON",
                "AM:STAT ON",
            ):
                self.instrument.write(command)

    def set_frequency(self, frequency_ghz: float) -> None:
        """Change only the carrier frequency while AM/LFO/RF remain enabled."""
        frequency_ghz = float(frequency_ghz)
        if not math.isfinite(frequency_ghz) or frequency_ghz <= 0.0:
            raise ValueError("SMB100A 微波频率必须是有限正数")
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("SMB100A 尚未连接")
            self.instrument.write(f"FREQ {frequency_ghz:.9g}GHz")

    def rf_off(self) -> None:
        self.safe_output_off()


_MFLI_BRIDGE_PREFIX = "MOKE_MFLI_JSON:"


_MFLI_BRIDGE_SOURCE = r'''
from __future__ import print_function

import json
import math
import sys
import time
import traceback

PREFIX = "MOKE_MFLI_JSON:"
DEVICE_REQUEST = sys.argv[1]
API_LEVEL = int(sys.argv[2])
DEMOD = int(sys.argv[3])
EXTREF_DEMOD = 1
EXTREF = 0


def emit(payload):
    sys.stdout.write(PREFIX + json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def scalar(sample, field, required=True):
    try:
        value = sample[field]
    except (KeyError, TypeError):
        if required:
            raise RuntimeError("MFLI sample missing field: " + field)
        return None
    try:
        value = value[-1]
    except (IndexError, KeyError, TypeError):
        pass
    result = float(value)
    if not math.isfinite(result):
        raise RuntimeError("MFLI sample has non-finite field: " + field)
    return result


def input_state(reset_extrema=False):
    range_node = "/%s/sigins/0/range" % device
    max_node = "/%s/sigins/0/max" % device
    min_node = "/%s/sigins/0/min" % device
    result = {"input_range_v": float(daq.getDouble(range_node))}
    try:
        result["input_max_normalized"] = float(daq.getDouble(max_node))
        result["input_min_normalized"] = float(daq.getDouble(min_node))
        if reset_extrema:
            daq.setDouble(max_node, 0.0)
            daq.setDouble(min_node, 0.0)
    except Exception:
        result["input_max_normalized"] = None
        result["input_min_normalized"] = None
    return result


def run_autorange(timeout_s=8.0):
    old_range = float(daq.getDouble("/%s/sigins/0/range" % device))
    autorange_node = "/%s/sigins/0/autorange" % device
    daq.setInt(autorange_node, 1)
    deadline = time.monotonic() + float(timeout_s)
    while int(daq.getInt(autorange_node)) != 0:
        if time.monotonic() >= deadline:
            raise RuntimeError("MFLI Signal Input 1 autorange timed out")
        time.sleep(0.02)
    daq.sync()
    result = input_state(reset_extrema=True)
    result["old_input_range_v"] = old_range
    return result


daq = None
device = ""
configured_extref_demod = DEMOD
try:
    import zhinst.utils as zi_utils

    session = zi_utils.create_api_session(DEVICE_REQUEST, API_LEVEL)
    if not isinstance(session, tuple) or len(session) < 3:
        raise RuntimeError("Unexpected create_api_session result: %r" % (session,))
    daq, discovered, props = session[:3]
    device = str(discovered).strip().lower()
    if not device:
        raise RuntimeError("LabOne Discovery returned an empty device name")
    try:
        zi_utils.api_server_version_check(daq)
    except AttributeError:
        pass
    enable_node = "/%s/demods/%d/enable" % (device, DEMOD)
    daq.setInt(enable_node, 1)
    daq.sync()
    daq.getSample("/%s/demods/%d/sample" % (device, DEMOD))
    version = ""
    options = ""
    if isinstance(props, dict):
        version = str(props.get("serverversion") or props.get("version") or "")
        raw_options = props.get("options") or ""
        if isinstance(raw_options, (tuple, list)):
            options = ",".join(str(item) for item in raw_options)
        else:
            options = str(raw_options)
    if not options:
        try:
            options = str(daq.getString("/%s/features/options" % device))
        except Exception:
            pass
    try:
        daq.getInt("/%s/demods/1/enable" % device)
        has_demod_2 = True
    except Exception:
        has_demod_2 = False
    identity = "Zurich Instruments MFLI - " + device
    if version:
        identity += " - LabOne " + version
    emit({"ok": True, "event": "ready", "device": device,
          "version": version, "options": options,
          "has_demod_2": has_demod_2, "identity": identity})
except BaseException:
    emit({"ok": False, "event": "ready", "error": traceback.format_exc()})
    try:
        if daq is not None:
            daq.disconnect()
    except BaseException:
        pass
    sys.exit(2)

for request_line in sys.stdin:
    try:
        request = json.loads(request_line)
        command = request.get("command")
        if command == "configure":
            requested_ac = 1 if str(request["coupling"]).upper() == "AC" else 0
            requested_imp50 = int(request.get("input_imp50", 0))
            requested_scaling = float(request.get("input_scaling", 1.0))
            requested_harmonic = int(request["harmonic"])
            requested_reference_input = int(request["reference_adcselect"])
            requested_timeconstant = float(request["timeconstant_s"])
            requested_order = int(request["filter_order"])
            requested_rate = float(request["sample_rate_hz"])
            extref_demod = int(request.get("extref_demod", EXTREF_DEMOD))
            configured_extref_demod = extref_demod
            ac_node = "/%s/sigins/0/ac" % device
            imp50_node = "/%s/sigins/0/imp50" % device
            scaling_node = "/%s/sigins/0/scaling" % device
            harmonic_node = "/%s/demods/%d/harmonic" % (device, DEMOD)
            enable_node = "/%s/demods/%d/enable" % (device, DEMOD)
            osc_node = "/%s/demods/%d/oscselect" % (device, DEMOD)
            extref_demod_node = "/%s/extrefs/%d/demodselect" % (device, EXTREF)
            extref_auto_node = "/%s/extrefs/%d/automode" % (device, EXTREF)
            extref_enable_node = "/%s/extrefs/%d/enable" % (device, EXTREF)
            extref_adc_node = "/%s/extrefs/%d/adcselect" % (device, EXTREF)
            measurement_adc_node = "/%s/demods/%d/adcselect" % (device, DEMOD)
            timeconstant_node = "/%s/demods/%d/timeconstant" % (device, DEMOD)
            order_node = "/%s/demods/%d/order" % (device, DEMOD)
            rate_node = "/%s/demods/%d/rate" % (device, DEMOD)
            daq.setInt(ac_node, requested_ac)
            daq.setInt(imp50_node, requested_imp50)
            daq.setDouble(scaling_node, requested_scaling)
            ext_demod_node = "/%s/demods/%d/adcselect" % (device, extref_demod)
            ext_osc_node = "/%s/demods/%d/oscselect" % (device, extref_demod)
            ext_harmonic_node = "/%s/demods/%d/harmonic" % (device, extref_demod)
            ext_demod_enable_node = "/%s/demods/%d/enable" % (device, extref_demod)
            daq.setInt(ext_demod_node, requested_reference_input)
            daq.setInt(ext_osc_node, 0)
            if extref_demod != DEMOD:
                daq.setInt(ext_harmonic_node, 1)
            daq.setInt(ext_demod_enable_node, 1)
            selected_extref_input = int(daq.getInt(ext_demod_node))
            try:
                daq.setInt(extref_demod_node, extref_demod)
            except Exception:
                pass
            # automode/demodselect/locked were exposed as dedicated nodes in
            # newer LabOne releases.  LabOne 18.12 can still enable ExtRef,
            # but does not necessarily expose all three nodes to the API.
            try:
                daq.setInt(extref_auto_node, 4)
            except Exception:
                pass
            daq.setInt(extref_enable_node, 1)
            # A base MFLI temporarily uses Demod 1 to select/latch the chosen
            # external reference, then returns its measurement input to
            # Signal In 1.
            if extref_demod == DEMOD:
                daq.sync()
                if selected_extref_input != requested_reference_input:
                    raise RuntimeError(
                        "Requested external reference was not selected: "
                        "%d != %d" %
                        (selected_extref_input, requested_reference_input)
                    )
                daq.setInt(measurement_adc_node, 0)
            daq.setInt(osc_node, 0)
            daq.setInt(harmonic_node, requested_harmonic)
            daq.setDouble(timeconstant_node, requested_timeconstant)
            daq.setInt(order_node, requested_order)
            daq.setDouble(rate_node, requested_rate)
            daq.setInt(enable_node, 1)
            daq.sync()
            if extref_demod != DEMOD:
                try:
                    selected_extref_input = int(daq.getInt(extref_adc_node))
                except Exception:
                    pass
            configured_input = input_state(reset_extrema=True)
            emit({"ok": True, "actual_ac": int(daq.getInt(ac_node)),
                  "actual_imp50": int(daq.getInt(imp50_node)),
                  "actual_scaling": float(daq.getDouble(scaling_node)),
                  "actual_harmonic": int(daq.getInt(harmonic_node)),
                  "actual_extref_harmonic": (
                      int(daq.getInt(ext_harmonic_node))
                      if extref_demod != DEMOD else 1),
                  "extref_enabled": int(daq.getInt(extref_enable_node)),
                  "extref_adcselect": selected_extref_input,
                  "timeconstant_s": float(daq.getDouble(timeconstant_node)),
                  "filter_order": int(daq.getInt(order_node)),
                  "sample_rate_hz": float(daq.getDouble(rate_node)),
                  "input_range_v": configured_input["input_range_v"]})
        elif command == "read":
            sample = daq.getSample("/%s/demods/%d/sample" % (device, DEMOD))
            payload = {"x": scalar(sample, "x"), "y": scalar(sample, "y")}
            frequency = scalar(sample, "frequency", required=False)
            flags = scalar(sample, "flags", required=False)
            if frequency is not None:
                payload["frequency"] = frequency
            if flags is not None:
                payload["flags"] = flags
            try:
                payload["extref_locked"] = int(daq.getInt(
                    "/%s/extrefs/%d/locked" % (device, EXTREF)))
            except Exception:
                payload["extref_locked"] = None
            payload.update(input_state(reset_extrema=True))
            try:
                payload["device_status_flags"] = int(daq.getInt(
                    "/%s/status/flags/binary" % device))
            except Exception:
                payload["device_status_flags"] = None
            emit({"ok": True, "sample": payload})
        elif command == "autorange":
            emit({"ok": True, "autorange": run_autorange()})
        elif command == "close":
            try:
                daq.setInt("/%s/extrefs/%d/enable" % (device, EXTREF), 0)
                daq.setInt("/%s/demods/%d/enable" % (device, DEMOD), 0)
                if configured_extref_demod != DEMOD:
                    daq.setInt("/%s/demods/%d/enable" %
                               (device, configured_extref_demod), 0)
                daq.sync()
            finally:
                daq.disconnect()
            emit({"ok": True})
            break
        else:
            raise ValueError("Unknown bridge command: %r" % (command,))
    except BaseException:
        emit({"ok": False, "error": traceback.format_exc()})
'''


def _find_mfli_legacy_python() -> Path:
    """Locate and validate the exact Python 3.7/LabOne 18.12 environment."""
    configured = os.environ.get(NONLOCAL_MFLI_LEGACY_PYTHON_ENV, "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured.strip('"')))
    system_drive = os.environ.get("SystemDrive", "C:")
    drive_root = Path(
        system_drive + os.sep if system_drive.endswith(":") else system_drive
    )
    user_profile = os.environ.get("USERPROFILE", "")
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "NBB/MOKE_MFLI_PY37/python.exe"
        )
    candidates.append(
        Path(__file__).resolve().parent / ".mfli_py37/python.exe"
    )
    candidates.extend(
        (
            drive_root
            / "ProgramData/miniconda3/envs/GMS_VENV_PYTHON/python.exe",
            drive_root
            / "ProgramData/anaconda3/envs/GMS_VENV_PYTHON/python.exe",
        )
    )
    if user_profile:
        candidates.extend(
            (
                Path(user_profile)
                / "miniconda3/envs/GMS_VENV_PYTHON/python.exe",
                Path(user_profile)
                / "anaconda3/envs/GMS_VENV_PYTHON/python.exe",
            )
        )
    rejected = []
    validation_code = (
        "import sys,pkg_resources,numpy,zhinst,zhinst.utils;"
        "v=pkg_resources.get_distribution('zhinst').version;"
        "assert sys.version_info[:2]==(3,7),sys.version;"
        "assert v=='18.12.59443',v;"
        "assert numpy.__version__=='1.21.6',numpy.__version__;print(v)"
    )
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            check = subprocess.run(
                [str(candidate), "-c", validation_code],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8.0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if check.returncode == 0:
                return candidate
            detail = (check.stderr or check.stdout or "验证失败").strip()
            rejected.append(f"{candidate}（{short_status_error(detail, 160)}）")
        except (OSError, subprocess.SubprocessError) as error:
            rejected.append(f"{candidate}（{short_status_error(error, 160)}）")
            continue
    searched = "；".join(str(path) for path in candidates)
    rejected_text = "；".join(rejected) if rejected else "无"
    raise RuntimeError(
        "找不到通过验证的 Python 3.7 + zhinst 18.12.59443 环境。请先运行 "
        r"INSTALL_MFLI_LABONE_18_12.bat，或确认 "
        r"C:\ProgramData\miniconda3\envs\GMS_VENV_PYTHON\python.exe "
        f"存在，或设置环境变量 {NONLOCAL_MFLI_LEGACY_PYTHON_ENV}。"
        f"已检查：{searched}。不合格环境：{rejected_text}"
    )


class LegacyMFLIBridge:
    """JSON-line bridge from the Python 3.11 GUI to old zhinst/Python 3.7."""

    def __init__(self, device_id: str, timeout_s: float = 20.0) -> None:
        self.python_path = _find_mfli_legacy_python()
        self.process = None
        self.responses: queue.Queue = queue.Queue()
        self.diagnostics: list[str] = []
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        command = [
            str(self.python_path),
            "-u",
            "-c",
            _MFLI_BRIDGE_SOURCE,
            str(device_id),
            str(NONLOCAL_MFLI_API_LEVEL),
            str(NONLOCAL_MFLI_DEMODULATOR),
        ]
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
            threading.Thread(
                target=self._read_stdout,
                name="MFLI-bridge-stdout",
                daemon=True,
            ).start()
            threading.Thread(
                target=self._read_stderr,
                name="MFLI-bridge-stderr",
                daemon=True,
            ).start()
            ready = self._receive(timeout_s)
            if ready.get("event") != "ready":
                raise RuntimeError(f"MFLI 桥接启动回复异常：{ready!r}")
            self.device = str(ready.get("device", device_id)).strip().lower()
            self.version = str(ready.get("version", ""))
            self.options = str(ready.get("options", ""))
            self.has_demod_2 = bool(ready.get("has_demod_2", False))
            self.identity = str(ready.get("identity", self.device))
        except BaseException:
            self.close(force=True)
            raise

    def _append_diagnostic(self, line: str) -> None:
        line = str(line).strip()
        if not line:
            return
        self.diagnostics.append(line)
        del self.diagnostics[:-12]

    def _read_stdout(self) -> None:
        stream = self.process.stdout if self.process is not None else None
        if stream is None:
            self.responses.put({"_eof": True})
            return
        try:
            for raw_line in stream:
                line = raw_line.rstrip("\r\n")
                if line.startswith(_MFLI_BRIDGE_PREFIX):
                    try:
                        self.responses.put(
                            json.loads(line[len(_MFLI_BRIDGE_PREFIX):])
                        )
                    except BaseException as error:
                        self._append_diagnostic(f"JSON 解析失败：{error}: {line}")
                else:
                    self._append_diagnostic(line)
        finally:
            self.responses.put({"_eof": True})

    def _read_stderr(self) -> None:
        stream = self.process.stderr if self.process is not None else None
        if stream is None:
            return
        for line in stream:
            self._append_diagnostic(line)

    def _diagnostic_suffix(self) -> str:
        if not self.diagnostics:
            return ""
        return "；旧环境输出：" + " | ".join(self.diagnostics[-5:])

    def _receive(self, timeout_s: float) -> dict[str, object]:
        try:
            reply = self.responses.get(timeout=max(0.1, float(timeout_s)))
        except queue.Empty as error:
            raise RuntimeError(
                f"旧版 LabOne 桥接在 {timeout_s:g} s 内没有回复"
                + self._diagnostic_suffix()
            ) from error
        if reply.get("_eof"):
            exit_code = self.process.poll() if self.process is not None else None
            raise RuntimeError(
                f"旧版 LabOne 桥接意外退出（code={exit_code}）"
                + self._diagnostic_suffix()
            )
        if not reply.get("ok"):
            detail = str(reply.get("error", "未知错误")).strip()
            raise RuntimeError(
                "旧版 LabOne 桥接失败：" + detail + self._diagnostic_suffix()
            )
        return reply

    def request(
        self, command: str, timeout_s: float = 10.0, **parameters
    ) -> dict[str, object]:
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError(
                "旧版 LabOne 桥接未运行" + self._diagnostic_suffix()
            )
        if self.process.stdin is None:
            raise RuntimeError("旧版 LabOne 桥接 stdin 不可用")
        payload = {"command": str(command)}
        payload.update(parameters)
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise RuntimeError(
                "无法向旧版 LabOne 桥接发送命令" + self._diagnostic_suffix()
            ) from error
        return self._receive(timeout_s)

    def close(self, force: bool = False) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None and not force:
            try:
                self.request("close", timeout_s=5.0)
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    force = True
            except BaseException:
                force = True
        if process.poll() is None and force:
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except BaseException:
                try:
                    process.kill()
                except BaseException:
                    pass
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except BaseException:
                pass
        self.process = None


class NonLocalACController:
    """Own one 6221 plus independently selectable voltage instruments."""

    def __init__(self, logger: Callable[[str], None]) -> None:
        self.logger = logger
        self.resource_manager = None
        self.source = None
        self.lockins: dict[str, object] = {}
        self.mfli_daq = None
        self.mfli_bridge: LegacyMFLIBridge | None = None
        self.mfli_device = ""
        self.mfli_props: dict[str, object] = {}
        self.mfli_config: dict[str, object] = {}
        self.nanovoltmeter = None
        self.source_identity = ""
        self.lockin_identities: dict[str, str] = {}
        self.mfli_identity = ""
        self.nanovoltmeter_identity = ""
        self.source_resource = ""
        self.lockin_resources: dict[str, str] = {}
        self.nanovoltmeter_resource = ""
        self.mfli_lease_resource = ""
        self.output_enabled = False
        self.source_output_mode = "AC"
        self.lock = threading.RLock()
        self.sensitivity_indices: dict[str, int] = {}
        self.low_range_counts: dict[str, int] = {}
        self.range_cooldowns: dict[str, int] = {}
        self.mfli_low_range_count = 0
        self.mfli_range_cooldown = 0
        self.mfli_initial_autorange_pending = False

    @property
    def mfli_connected(self) -> bool:
        return self.mfli_daq is not None or self.mfli_bridge is not None

    def _close_manager_if_unused(self) -> None:
        if (
            self.source is not None
            or self.lockins
            or self.nanovoltmeter is not None
            or self.resource_manager is None
        ):
            return
        manager = self.resource_manager
        self.resource_manager = None
        release_shared_visa_manager(manager)

    @staticmethod
    def _mfli_reachable_servers(
        timeout_s: float = 1.2,
    ) -> tuple[list[tuple[str, int]], list[str]]:
        """Return reachable LabOne endpoints without entering a 20 s API wait."""
        reachable = []
        diagnostics = []
        for host, port in NONLOCAL_MFLI_SERVER_CANDIDATES:
            try:
                with socket.create_connection((host, int(port)), timeout_s):
                    reachable.append((host, int(port)))
                    diagnostics.append(f"{host}:{port}=TCP可达")
            except OSError as error:
                diagnostics.append(f"{host}:{port}=不可达（{error}）")
        return reachable, diagnostics

    def connect_mfli(self, device_id: str) -> str:
        """Prefer the current Python API, with the legacy bridge as fallback."""
        requested = str(device_id).strip().lower()
        if not requested:
            raise ValueError("请输入 Zurich MFLI API 设备名，例如 dev4435")
        if requested.startswith("mf-"):
            requested = requested[3:]
        with self.lock:
            if self.mfli_connected:
                return self.mfli_identity
            direct_errors: list[str] = []
            reachable_servers, tcp_diagnostics = self._mfli_reachable_servers()
            self.logger("MFLI 网络预检：" + "；".join(tcp_diagnostics))

            # Do not start Core/Discovery or search for another Python when no
            # LabOne Data Server is listening.  Neither a pip install nor a
            # legacy interpreter can repair a disconnected USB/RNDIS link.
            if not reachable_servers:
                diagnostic_text = "；".join(tcp_diagnostics)
                raise RuntimeError(
                    "Zurich MFLI Data Server 当前不可达，程序尚未进入 "
                    "LabOne API 握手。\n\n"
                    f"快速检测：{diagnostic_text}\n\n"
                    "请依次检查：\n"
                    "1. MFLI 已开机，并用后面板 USB Device 口连接电脑；\n"
                    "2. Windows 的 USB/RNDIS 网卡已启用，电脑地址应为 "
                    "192.168.69.77；\n"
                    "3. PowerShell 执行：ping 192.168.69.78；\n"
                    "4. 执行：Test-NetConnection 192.168.69.78 -Port 8004。\n\n"
                    "只有 TcpTestSucceeded=True 后，Python API 才可能连接。"
                )

            mfli_lease_resource = f"LABONE::{requested}"
            acquire_instrument_lease(
                mfli_lease_resource, self, "Non-local Zurich MFLI"
            )
            self.mfli_lease_resource = mfli_lease_resource

            # LabOne 18.12 only published a CPython 3.7 wheel.  Prefer the
            # installer-created, version-exact bridge when it exists; the GUI,
            # camera, VISA and all other modules remain in the current Python.
            bridge = None
            try:
                bridge = LegacyMFLIBridge(requested)
                self.mfli_daq = None
                self.mfli_bridge = bridge
                self.mfli_device = bridge.device
                self.mfli_props = {
                    "serverversion": bridge.version,
                    "connection": "labone-18.12-python37-bridge",
                    "python": str(bridge.python_path),
                    "options": bridge.options,
                    "has_demod_2": bridge.has_demod_2,
                }
                self.mfli_config = {}
                self.mfli_identity = bridge.identity
                self.logger(
                    "Non-local Zurich MFLI 已通过精确匹配的 LabOne 18.12 "
                    f"环境连接：{bridge.identity}"
                )
                return bridge.identity
            except BaseException as bridge_error:
                if bridge is not None:
                    bridge.close(force=True)
                direct_errors.append(f"18.12 Python 3.7 桥接：{bridge_error}")
                self.logger(
                    "精确匹配的 LabOne 18.12 桥接暂不可用，继续尝试当前 "
                    f"Python 的本机 API：{bridge_error}"
                )

            # Then try version-matched native APIs installed with LabOne or its
            # LabVIEW driver. Both adapters run inside this Python process.
            if sys.platform == "win32":
                native_adapters = (
                    ("LabOne C API", LabOneCAPIAdapter,
                     "current-python-labone-c-api"),
                    ("LabOne .NET", LabOneDotNetAdapter,
                     "current-python-labone-dotnet"),
                )
                for adapter_label, adapter_class, connection_name in native_adapters:
                    for server_host, server_port in reachable_servers:
                        daq = None
                        try:
                            daq = adapter_class(
                                server_host,
                                server_port,
                                NONLOCAL_MFLI_API_LEVEL,
                            )
                            sample_node = (
                                f"/{requested}/demods/"
                                f"{NONLOCAL_MFLI_DEMODULATOR}/sample"
                            )
                            enable_node = (
                                f"/{requested}/demods/"
                                f"{NONLOCAL_MFLI_DEMODULATOR}/enable"
                            )
                            try:
                                daq.setInt(enable_node, 1)
                                daq.sync()
                                daq.getSample(sample_node)
                            except BaseException as initial_error:
                                attachment_errors = [f"已挂接读取：{initial_error}"]
                                for interface in ("PCIe", "1GbE", "USB"):
                                    try:
                                        daq.connectDevice(requested, interface)
                                        daq.setInt(enable_node, 1)
                                        daq.sync()
                                        daq.getSample(sample_node)
                                        break
                                    except BaseException as attach_error:
                                        attachment_errors.append(
                                            f"{interface}: {attach_error}"
                                        )
                                else:
                                    raise RuntimeError(
                                        "Data Server 已连接，但无法读取 DEV4435："
                                        + " | ".join(attachment_errors)
                                    )
                            version = ""
                            for version_node in (
                                "/zi/about/version",
                                "/zi/about/revision",
                            ):
                                try:
                                    version = str(daq.getString(version_node)).strip()
                                except BaseException:
                                    continue
                                if version:
                                    break
                            identity_parts = ["Zurich Instruments MFLI", requested]
                            if version:
                                identity_parts.append(f"LabOne {version}")
                            identity = " · ".join(identity_parts)
                            self.mfli_daq = daq
                            self.mfli_bridge = None
                            self.mfli_device = requested
                            self.mfli_props = {
                                "serveraddress": server_host,
                                "serverport": server_port,
                                "serverversion": version,
                                "connection": connection_name,
                                "api_dll": str(daq.api_dll),
                            }
                            self.mfli_config = {}
                            self.mfli_identity = identity
                            self.logger(
                                "Non-local Zurich MFLI 已由当前 Python 加载 "
                                f"{adapter_label}：{identity} @ "
                                f"{server_host}:{server_port}"
                            )
                            return identity
                        except BaseException as error:
                            direct_errors.append(
                                f"{adapter_label} {server_host}:{server_port}："
                                f"{error}"
                            )
                            if daq is not None:
                                try:
                                    daq.disconnect()
                                except BaseException:
                                    pass
                            self.logger(
                                f"当前 Python 加载 {adapter_label} 连接 "
                                f"{server_host}:{server_port} 失败：{error}"
                            )

            # Modern Core remains a secondary path, useful after LabOne is
            # upgraded or when the matching .NET API was not installed.
            if zi_core is not None:
                for server_host, server_port in reachable_servers:
                    if server_host == NONLOCAL_MFLI_SERVER_HOST:
                        direct_errors.append(
                            "新版 Core：已跳过已确认运行 LabOne 18.12.59443 "
                            f"的 {server_host}:{server_port}，避免重复等待 20 s"
                        )
                        continue
                    daq = None
                    try:
                        daq = zi_core.ziDAQServer(
                            server_host,
                            server_port,
                            NONLOCAL_MFLI_API_LEVEL,
                            allow_version_mismatch=True,
                        )
                        sample_node = (
                            f"/{requested}/demods/"
                            f"{NONLOCAL_MFLI_DEMODULATOR}/sample"
                        )
                        try:
                            visible_nodes = daq.listNodes(
                                f"/{requested}", recursive=False, absolute=True
                            )
                        except TypeError:
                            visible_nodes = daq.listNodes(f"/{requested}", 0)
                        if not visible_nodes:
                            attachment_errors = []
                            for interface in ("PCIe", "1GbE", "USB"):
                                try:
                                    daq.connectDevice(requested, interface)
                                    break
                                except BaseException as attach_error:
                                    attachment_errors.append(
                                        f"{interface}: {attach_error}"
                                    )
                            else:
                                raise RuntimeError(
                                    "Data Server 已连接，但无法挂接设备："
                                    + " | ".join(attachment_errors)
                                )
                        daq.setInt(
                            f"/{requested}/demods/"
                            f"{NONLOCAL_MFLI_DEMODULATOR}/enable",
                            1,
                        )
                        daq.sync()
                        daq.getSample(sample_node)
                        version = ""
                        for version_node in (
                            "/zi/about/version",
                            "/zi/about/revision",
                        ):
                            try:
                                version = str(daq.getString(version_node)).strip()
                            except BaseException:
                                continue
                            if version:
                                break
                        identity_parts = ["Zurich Instruments MFLI", requested]
                        if version:
                            identity_parts.append(f"LabOne {version}")
                        identity = " · ".join(identity_parts)
                        self.mfli_daq = daq
                        self.mfli_bridge = None
                        self.mfli_device = requested
                        self.mfli_props = {
                            "serveraddress": server_host,
                            "serverport": server_port,
                            "serverversion": version,
                            "connection": "current-python-core",
                            "allow_version_mismatch": True,
                        }
                        self.mfli_config = {}
                        self.mfli_identity = identity
                        self.logger(
                            "Non-local Zurich MFLI 已由当前 Python Core API "
                            f"直接连接：{identity} @ {server_host}:{server_port}"
                        )
                        return identity
                    except BaseException as error:
                        direct_errors.append(
                            f"Core {server_host}:{server_port}：{error}"
                        )
                        if daq is not None:
                            try:
                                daq.disconnect()
                            except BaseException:
                                pass
                        self.logger(
                            f"当前 Python Core API 连接 {server_host}:{server_port} "
                            f"失败：{error}"
                        )

            # Discovery is only worth attempting after a server endpoint has
            # replied.  Otherwise it adds another long timeout without adding
            # diagnostic value.
            if zi_core is None and zi_utils is not None and reachable_servers:
                daq = None
                try:
                    session = zi_utils.create_api_session(
                        requested, NONLOCAL_MFLI_API_LEVEL
                    )
                    if not isinstance(session, tuple) or len(session) < 3:
                        raise RuntimeError(
                            f"create_api_session 返回格式异常：{session!r}"
                        )
                    daq, discovered, props = session[:3]
                    device = str(discovered).strip().lower()
                    if not device:
                        raise RuntimeError("LabOne Discovery 未返回 MFLI 设备名")
                    daq.setInt(
                        f"/{device}/demods/"
                        f"{NONLOCAL_MFLI_DEMODULATOR}/enable",
                        1,
                    )
                    daq.sync()
                    daq.getSample(
                        f"/{device}/demods/"
                        f"{NONLOCAL_MFLI_DEMODULATOR}/sample"
                    )
                    identity_parts = ["Zurich Instruments MFLI", device]
                    if isinstance(props, dict):
                        version = props.get("serverversion") or props.get("version")
                        if version:
                            identity_parts.append(f"LabOne {version}")
                    identity = " · ".join(identity_parts)
                    self.mfli_daq = daq
                    self.mfli_bridge = None
                    self.mfli_device = device
                    self.mfli_props = (
                        dict(props) if isinstance(props, dict) else {}
                    )
                    self.mfli_props["connection"] = "current-python-discovery"
                    self.mfli_config = {}
                    self.mfli_identity = identity
                    self.logger(
                        "Non-local Zurich MFLI 已由当前 Python Discovery "
                        f"连接：{identity}"
                    )
                    return identity
                except BaseException as error:
                    direct_errors.append(f"Discovery：{error}")
                    if daq is not None:
                        try:
                            daq.disconnect()
                        except BaseException:
                            pass
                    self.logger(
                        "当前 Python Discovery 失败，最后尝试旧环境桥接："
                        f"{error}"
                    )

            direct_detail = " | ".join(direct_errors)
            release_instrument_lease(self.mfli_lease_resource, self)
            self.mfli_lease_resource = ""
            raise RuntimeError(
                "Zurich MFLI 的 TCP 8004 已可达，但 LabOne API 握手失败。\n"
                f"连接诊断：{direct_detail}\n\n"
                "该仪器运行 LabOne 18.12.59443；请先关闭本程序，运行同目录 "
                "INSTALL_MFLI_LABONE_18_12.bat，安装完成后重启程序。"
            )

    @staticmethod
    def _mfli_scalar(value, field: str) -> float:
        """Extract one finite scalar from old/new LabOne sample containers."""
        try:
            candidate = value[field]
        except (KeyError, TypeError) as error:
            raise RuntimeError(f"MFLI sample 缺少 {field!r} 字段") from error
        if np is not None:
            array = np.asarray(candidate).reshape(-1)
            if array.size == 0:
                raise RuntimeError(f"MFLI sample 的 {field!r} 字段为空")
            result = float(array[-1])
        elif isinstance(candidate, (tuple, list)):
            if not candidate:
                raise RuntimeError(f"MFLI sample 的 {field!r} 字段为空")
            result = float(candidate[-1])
        else:
            result = float(candidate)
        if not math.isfinite(result):
            raise RuntimeError(f"MFLI sample 的 {field!r} 字段不是有限数值")
        return result

    @staticmethod
    def _mfli_phase_degrees(x: float, y: float) -> float:
        """Return the LabOne demodulator phase from independent X and Y."""
        return math.degrees(math.atan2(float(y), float(x)))

    def configure_mfli(
        self,
        coupling: str,
        harmonic: int,
        channel: str,
        reference_source: str = NONLOCAL_MFLI_DEFAULT_REFERENCE_INPUT,
        sample_rate_hz: float = NONLOCAL_SAMPLE_HZ,
        input_impedance: str = "10 MΩ",
        input_scaling: float = NONLOCAL_MFLI_INPUT_SCALING,
        timeconstant_s: float = NONLOCAL_LOCKIN_TIME_CONSTANT_S,
    ) -> dict[str, object]:
        """Route the selected external reference, then configure Demod 1."""
        if not self.mfli_connected:
            raise RuntimeError("Zurich MFLI 尚未连接")
        coupling = str(coupling).strip().upper()
        channel = str(channel).strip().upper()
        reference_source = str(reference_source).strip()
        harmonic = int(harmonic)
        sample_rate_hz = float(sample_rate_hz)
        input_impedance = str(input_impedance).strip()
        input_scaling = float(input_scaling)
        timeconstant_s = float(timeconstant_s)
        if coupling not in {"AC", "DC"}:
            raise ValueError("MFLI 输入耦合只能选择 AC 或 DC")
        if harmonic < 1:
            raise ValueError("MFLI 倍频必须是正整数")
        if not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
            raise ValueError("MFLI 采样率必须是有限正数")
        transfer_rate_hz = max(10.0, min(1000.0, sample_rate_hz * 5.0))
        if channel not in {"X", "Y", "R"}:
            raise ValueError("MFLI 读取通道只能选择 X、Y 或 R")
        if reference_source not in NONLOCAL_MFLI_REFERENCE_INPUTS:
            choices = "、".join(NONLOCAL_MFLI_REFERENCE_INPUTS)
            raise ValueError(f"MFLI 外参考接口必须选择：{choices}")
        impedance_choices = {"10 MΩ": 0, "50 Ω": 1}
        if input_impedance not in impedance_choices:
            raise ValueError("MFLI Signal Input 1 输入阻抗只能选择 10 MΩ 或 50 Ω")
        requested_imp50 = impedance_choices[input_impedance]
        if not math.isfinite(input_scaling) or input_scaling <= 0:
            raise ValueError("MFLI Signal Input 1 Scaling 必须是有限正数")
        if not math.isfinite(timeconstant_s) or timeconstant_s <= 0:
            raise ValueError("MFLI 时间常数必须是有限正数")
        reference_adcselect = NONLOCAL_MFLI_REFERENCE_INPUTS[
            reference_source
        ]
        options_text = str(self.mfli_props.get("options", "")).upper()
        has_demod_2 = bool(self.mfli_props.get("has_demod_2", False))
        if self.mfli_bridge is None and not options_text:
            try:
                options_text = str(
                    self.mfli_daq.getString(
                        f"/{self.mfli_device}/features/options"
                    )
                ).upper()
                self.mfli_props["options"] = options_text
            except BaseException:
                pass
        if self.mfli_bridge is None and not has_demod_2:
            try:
                self.mfli_daq.getInt(
                    f"/{self.mfli_device}/demods/"
                    f"{NONLOCAL_MFLI_EXTREF_DEMODULATOR}/enable"
                )
                has_demod_2 = True
            except BaseException:
                has_demod_2 = False
            self.mfli_props["has_demod_2"] = has_demod_2
        # MF-MD provides a second demodulator that can act as the external-
        # reference PLL while Demod 1 measures Signal Input 1. On a base MFLI
        # the External Reference block performs that routing with Demod 1.
        has_multi_demod = (
            ("MD" in options_text) if options_text else has_demod_2
        )
        extref_demod = (
            NONLOCAL_MFLI_EXTREF_DEMODULATOR
            if has_multi_demod
            else NONLOCAL_MFLI_DEMODULATOR
        )
        if self.mfli_bridge is not None:
            reply = self.mfli_bridge.request(
                "configure",
                coupling=coupling,
                harmonic=harmonic,
                extref_demod=extref_demod,
                reference_adcselect=reference_adcselect,
                timeconstant_s=timeconstant_s,
                filter_order=NONLOCAL_LOCKIN_FILTER_ORDER,
                sample_rate_hz=transfer_rate_hz,
                input_imp50=requested_imp50,
                input_scaling=input_scaling,
            )
            actual_ac = int(reply["actual_ac"])
            actual_imp50 = int(reply["actual_imp50"])
            actual_scaling = float(reply["actual_scaling"])
            actual_harmonic = int(reply["actual_harmonic"])
            actual_extref_harmonic = int(reply["actual_extref_harmonic"])
            extref_enabled = int(reply["extref_enabled"])
            extref_adcselect = int(reply["extref_adcselect"])
            actual_timeconstant = float(reply["timeconstant_s"])
            actual_filter_order = int(reply["filter_order"])
            actual_sample_rate = float(reply["sample_rate_hz"])
            input_range_v = float(reply["input_range_v"])
        else:
            device = self.mfli_device
            demod = NONLOCAL_MFLI_DEMODULATOR
            extref = NONLOCAL_MFLI_EXTREF_CHANNEL
            ac_node = f"/{device}/sigins/0/ac"
            imp50_node = f"/{device}/sigins/0/imp50"
            scaling_node = f"/{device}/sigins/0/scaling"
            harmonic_node = f"/{device}/demods/{demod}/harmonic"
            enable_node = f"/{device}/demods/{demod}/enable"
            osc_node = f"/{device}/demods/{demod}/oscselect"
            extref_demod_node = f"/{device}/extrefs/{extref}/demodselect"
            extref_auto_node = f"/{device}/extrefs/{extref}/automode"
            extref_enable_node = f"/{device}/extrefs/{extref}/enable"
            extref_adc_node = f"/{device}/extrefs/{extref}/adcselect"
            measurement_adc_node = f"/{device}/demods/{demod}/adcselect"
            timeconstant_node = f"/{device}/demods/{demod}/timeconstant"
            order_node = f"/{device}/demods/{demod}/order"
            rate_node = f"/{device}/demods/{demod}/rate"
            self.mfli_daq.setInt(ac_node, 1 if coupling == "AC" else 0)
            self.mfli_daq.setInt(imp50_node, requested_imp50)
            self.mfli_daq.setDouble(scaling_node, input_scaling)
            ext_demod_node = (
                f"/{device}/demods/{extref_demod}/adcselect"
            )
            ext_osc_node = (
                f"/{device}/demods/{extref_demod}/oscselect"
            )
            ext_harmonic_node = (
                f"/{device}/demods/{extref_demod}/harmonic"
            )
            ext_demod_enable_node = (
                f"/{device}/demods/{extref_demod}/enable"
            )
            self.mfli_daq.setInt(ext_demod_node, reference_adcselect)
            self.mfli_daq.setInt(ext_osc_node, 0)
            # With MF-MD, Demod 2 is only the phase detector for the external
            # reference PLL.  Keep its harmonic at 1 so oscillator 1 locks to
            # the fundamental.  Demod 1 independently applies the requested
            # measurement harmonic (for example 2ω).
            if extref_demod != demod:
                self.mfli_daq.setInt(ext_harmonic_node, 1)
            self.mfli_daq.setInt(ext_demod_enable_node, 1)
            selected_extref_input = int(
                self.mfli_daq.getInt(ext_demod_node)
            )
            # With MF-MD, Demod 2 is the PLL phase detector and Demod 1
            # remains the measurement demodulator. Without MF-MD, extrefs/0
            # performs the one-demodulator External Reference switch itself.
            try:
                self.mfli_daq.setInt(extref_demod_node, extref_demod)
            except BaseException:
                # Some LabOne 18.12 builds expose this node as read-only; the
                # selected Aux-input demodulator is still detected by extrefs.
                pass
            try:
                self.mfli_daq.setInt(extref_auto_node, 4)
            except BaseException:
                pass
            self.mfli_daq.setInt(extref_enable_node, 1)
            if extref_demod == demod:
                self.mfli_daq.sync()
                if selected_extref_input != reference_adcselect:
                    raise RuntimeError(
                        f"MFLI 单解调器外参未能选择 {reference_source}："
                        f"期望 {reference_adcselect}，回读 "
                        f"{selected_extref_input}"
                    )
                self.mfli_daq.setInt(measurement_adc_node, 0)
            self.mfli_daq.setInt(osc_node, 0)
            self.mfli_daq.setInt(harmonic_node, harmonic)
            self.mfli_daq.setDouble(timeconstant_node, timeconstant_s)
            self.mfli_daq.setInt(order_node, NONLOCAL_LOCKIN_FILTER_ORDER)
            self.mfli_daq.setDouble(rate_node, transfer_rate_hz)
            self.mfli_daq.setInt(enable_node, 1)
            self.mfli_daq.sync()
            actual_ac = int(self.mfli_daq.getInt(ac_node))
            actual_imp50 = int(self.mfli_daq.getInt(imp50_node))
            actual_scaling = float(
                self.mfli_daq.getDouble(scaling_node)
            )
            actual_harmonic = int(self.mfli_daq.getInt(harmonic_node))
            actual_extref_harmonic = (
                int(self.mfli_daq.getInt(ext_harmonic_node))
                if extref_demod != demod
                else 1
            )
            extref_enabled = int(self.mfli_daq.getInt(extref_enable_node))
            input_range_v = float(
                self.mfli_daq.getDouble(f"/{device}/sigins/0/range")
            )
            actual_timeconstant = float(
                self.mfli_daq.getDouble(timeconstant_node)
            )
            actual_filter_order = int(self.mfli_daq.getInt(order_node))
            actual_sample_rate = float(self.mfli_daq.getDouble(rate_node))
            if extref_demod != demod:
                try:
                    selected_extref_input = int(
                        self.mfli_daq.getInt(extref_adc_node)
                    )
                except BaseException:
                    pass
            extref_adcselect = selected_extref_input
        if actual_ac != (1 if coupling == "AC" else 0):
            raise RuntimeError(f"MFLI AC/DC 回读不符：{actual_ac}")
        if actual_imp50 != requested_imp50:
            raise RuntimeError(
                "MFLI Signal Input 1 输入阻抗回读不符："
                f"期望{input_impedance}，imp50={actual_imp50}"
            )
        if not math.isclose(
            actual_scaling,
            input_scaling,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            raise RuntimeError(
                "MFLI Signal Input 1 Scaling回读不符："
                f"期望{input_scaling:g}，回读{actual_scaling:g}"
            )
        if actual_harmonic != harmonic:
            raise RuntimeError(
                f"MFLI 倍频回读不符：请求 {harmonic}，回读 {actual_harmonic}"
            )
        if actual_extref_harmonic != 1:
            raise RuntimeError(
                "MFLI 外参考PLL倍频必须为1："
                f"回读 {actual_extref_harmonic}"
            )
        if extref_enabled != 1:
            raise RuntimeError(
                f"MFLI {reference_source} 外部参考未成功启用"
            )
        if extref_adcselect != reference_adcselect:
            raise RuntimeError(
                "MFLI 外部参考输入回读不符："
                f"期望 {reference_source}({reference_adcselect})，"
                f"回读 {extref_adcselect}"
            )
        if not math.isclose(
            actual_timeconstant,
            timeconstant_s,
            rel_tol=0.05,
            abs_tol=1e-6,
        ):
            raise RuntimeError(
                "MFLI 时间常数回读不符："
                f"请求 {timeconstant_s:g} s，回读 {actual_timeconstant:g} s"
            )
        if actual_filter_order != NONLOCAL_LOCKIN_FILTER_ORDER:
            raise RuntimeError(
                f"MFLI 滤波阶数回读不符：{actual_filter_order}"
            )
        if not math.isfinite(actual_sample_rate) or actual_sample_rate < sample_rate_hz:
            raise RuntimeError(
                f"MFLI 数据传输率不足：{actual_sample_rate:g} Sa/s"
            )
        self.mfli_config = {
            "meter_type": "MFLI",
            "resource": self.mfli_device,
            "coupling": "AC" if actual_ac else "DC",
            "harmonic": actual_harmonic,
            "channel": channel,
            "reference_source": reference_source,
            "reference_adcselect": reference_adcselect,
            "extref_channel": NONLOCAL_MFLI_EXTREF_CHANNEL,
            "extref_demodulator": extref_demod,
            "extref_harmonic": actual_extref_harmonic,
            "input_range_v": input_range_v,
            "input_impedance": input_impedance,
            "input_scaling": actual_scaling,
            "auto_range": True,
            "timeconstant_s": actual_timeconstant,
            "filter_order": actual_filter_order,
            "sample_rate_hz": actual_sample_rate,
        }
        self.mfli_low_range_count = 0
        self.mfli_range_cooldown = 0
        self.mfli_initial_autorange_pending = True
        return dict(self.mfli_config)

    def read_mfli(self, expected_frequency_hz: float) -> dict[str, object]:
        if not self.mfli_connected:
            raise RuntimeError("Zurich MFLI 尚未连接")
        if self.mfli_bridge is not None:
            sample = self.mfli_bridge.request("read")["sample"]
        else:
            sample = self.mfli_daq.getSample(
                f"/{self.mfli_device}/demods/"
                f"{NONLOCAL_MFLI_DEMODULATOR}/sample"
            )
            sample = dict(sample)
            try:
                sample["device_status_flags"] = int(self.mfli_daq.getInt(
                    f"/{self.mfli_device}/status/flags/binary"
                ))
            except Exception:
                sample["device_status_flags"] = None
            try:
                sample["extref_locked"] = int(
                    self.mfli_daq.getInt(
                        f"/{self.mfli_device}/extrefs/"
                        f"{NONLOCAL_MFLI_EXTREF_CHANNEL}/locked"
                    )
                )
            except BaseException:
                sample["extref_locked"] = None
            range_node = f"/{self.mfli_device}/sigins/0/range"
            max_node = f"/{self.mfli_device}/sigins/0/max"
            min_node = f"/{self.mfli_device}/sigins/0/min"
            sample["input_range_v"] = float(
                self.mfli_daq.getDouble(range_node)
            )
            try:
                sample["input_max_normalized"] = float(
                    self.mfli_daq.getDouble(max_node)
                )
                sample["input_min_normalized"] = float(
                    self.mfli_daq.getDouble(min_node)
                )
                self.mfli_daq.setDouble(max_node, 0.0)
                self.mfli_daq.setDouble(min_node, 0.0)
            except BaseException:
                sample["input_max_normalized"] = None
                sample["input_min_normalized"] = None
        x = self._mfli_scalar(sample, "x")
        y = self._mfli_scalar(sample, "y")
        r = math.hypot(x, y)
        phase_deg = self._mfli_phase_degrees(x, y)
        harmonic = int(self.mfli_config.get("harmonic", 1))
        reported_frequency = math.nan
        try:
            reported_frequency = self._mfli_scalar(sample, "frequency")
            # LabOne generations differ in whether this sample field is
            # described as oscillator/reference frequency or effective demod
            # frequency.  Accept either representation and normalize the CSV
            # value back to the 6221 fundamental.
            candidates = (reported_frequency, reported_frequency / max(harmonic, 1))
            reference_frequency = min(
                candidates,
                key=lambda value: abs(value - expected_frequency_hz),
            )
        except RuntimeError:
            reference_frequency = float(expected_frequency_hz)
        frequency_valid = math.isclose(
            reference_frequency,
            expected_frequency_hz,
            rel_tol=5e-3,
            abs_tol=0.1,
        )
        raw_extref_locked = sample.get("extref_locked")
        extref_locked = (
            None if raw_extref_locked is None else bool(round(float(raw_extref_locked)))
        )
        reference_valid = frequency_valid and extref_locked is not False
        try:
            flags = int(round(self._mfli_scalar(sample, "flags")))
        except RuntimeError:
            flags = 0
        status_parts = []
        # Device status bits, NOT demod sample flags or R/input-range estimates.
        # https://docs.zhinst.com/mfli_user_manual/nodedoc.html
        raw_device_flags = sample.get("device_status_flags")
        try:
            device_flags = int(raw_device_flags) if raw_device_flags is not None else None
            if device_flags is not None and device_flags < 0:
                device_flags = None
        except (TypeError, ValueError, OverflowError):
            device_flags = None
        input_overload = None if device_flags is None else bool(device_flags & 0x3)
        if input_overload:
            status_parts.append("MFLI input OVERLOAD")
        if flags:
            status_parts.append(f"LabOne flags={flags}")
        if extref_locked is False:
            reference_source = str(
                self.mfli_config.get(
                    "reference_source",
                    NONLOCAL_MFLI_DEFAULT_REFERENCE_INPUT,
                )
            )
            status_parts.append(f"{reference_source} 外部参考未锁定")
        elif extref_locked is None and frequency_valid:
            status_parts.append("LabOne 18.12 按参考频率收敛判定 LOCK")
        if not frequency_valid:
            status_parts.append("参考频率不符")
        input_range_v = self._mfli_scalar(sample, "input_range_v")
        input_extrema = []
        for field in ("input_max_normalized", "input_min_normalized"):
            try:
                input_extrema.append(abs(self._mfli_scalar(sample, field)))
            except (RuntimeError, TypeError, ValueError):
                pass
        input_utilization = (
            max(input_extrema)
            if input_extrema
            else min(1.0, abs(r) / max(input_range_v, 1e-30))
        )
        return {
            "meter_type": "MFLI",
            "voltage": {"X": x, "Y": y, "R": r}.get(
                str(self.mfli_config.get("channel", "R")), r
            ),
            "X": x,
            "Y": y,
            "R": r,
            "phase_deg": phase_deg,
            "reference_frequency_hz": reference_frequency,
            "demodulation_frequency_hz": reported_frequency,
            "reference_source": str(
                self.mfli_config.get(
                    "reference_source",
                    NONLOCAL_MFLI_DEFAULT_REFERENCE_INPUT,
                )
            ),
            "extref_locked": extref_locked,
            "lia_status": flags | (0 if reference_valid else 8),
            "status_text": "; ".join(status_parts) if status_parts else "OK",
            # LabOne sample flags are reported separately.  Reference LOCK is
            # determined by extrefs/0/locked plus the frequency comparison;
            # unrelated warning bits must not turn a valid PLL into WARNING.
            "reference_valid": reference_valid,
            "sensitivity_index": "",
            "input_range_v": input_range_v,
            "input_utilization": input_utilization,
            "input_extrema_valid": len(input_extrema) == 2 and all(
                math.isfinite(value) for value in input_extrema
            ),
            "device_status_flags": device_flags,
            "input_overload": input_overload,
            "auto_range": "",
        }

    def _run_mfli_autorange(self, reason: str) -> str:
        """Run the MFLI firmware autorange and verify its range readback."""
        if not self.mfli_connected:
            raise RuntimeError("Zurich MFLI 尚未连接")
        configured_old = float(self.mfli_config.get("input_range_v", math.nan))
        if self.mfli_bridge is not None:
            result = self.mfli_bridge.request("autorange")["autorange"]
            old_range = float(result["old_input_range_v"])
            new_range = float(result["input_range_v"])
        else:
            device = self.mfli_device
            range_node = f"/{device}/sigins/0/range"
            autorange_node = f"/{device}/sigins/0/autorange"
            old_range = float(self.mfli_daq.getDouble(range_node))
            self.mfli_daq.setInt(autorange_node, 1)
            deadline = time.monotonic() + 8.0
            while int(self.mfli_daq.getInt(autorange_node)) != 0:
                if time.monotonic() >= deadline:
                    raise RuntimeError("MFLI Signal Input 1 自动量程超时")
                time.sleep(0.02)
            self.mfli_daq.sync()
            new_range = float(self.mfli_daq.getDouble(range_node))
            for suffix in ("max", "min"):
                try:
                    self.mfli_daq.setDouble(
                        f"/{device}/sigins/0/{suffix}", 0.0
                    )
                except BaseException:
                    pass
        if not math.isfinite(old_range) or old_range <= 0:
            old_range = configured_old
        if not math.isfinite(new_range) or new_range <= 0:
            raise RuntimeError(f"MFLI 自动量程回读无效：{new_range!r}")
        self.mfli_config["input_range_v"] = new_range
        self.mfli_low_range_count = 0
        self.mfli_range_cooldown = 3
        self.mfli_initial_autorange_pending = False
        return f"RANGE {old_range:.6g}->{new_range:.6g} V ({reason})"

    def _auto_range_mfli(self, sample: dict[str, object]) -> str:
        """Continuously adapt Signal Input 1 using raw ADC utilization."""
        utilization = float(sample.get("input_utilization", 0.0))
        if not math.isfinite(utilization):
            return ""
        if utilization >= 0.85:
            return self._run_mfli_autorange("overload/high")
        if self.mfli_range_cooldown > 0:
            self.mfli_range_cooldown -= 1
            self.mfli_low_range_count = 0
            return ""
        if utilization < 0.15:
            self.mfli_low_range_count += 1
            if self.mfli_low_range_count >= 3:
                return self._run_mfli_autorange("low")
        else:
            self.mfli_low_range_count = 0
        return ""

    def read_stfmr_mfli(
        self, expected_frequency_hz: float, *, auto_range: bool,
        initial: bool = False, attempts: int = 0,
    ) -> dict[str, object]:
        """Worker-only range control. Caller must discard/settle/re-read on action.

        Raw input extrema, not demodulated R, determine ongoing autoranging.
        The existing firmware operation supports both native and legacy APIs.
        """
        sample = self.read_mfli(expected_frequency_hz)
        if not auto_range:
            return sample
        utilization = float(sample.get("input_utilization", math.nan))
        raw_valid = bool(sample.get("input_extrema_valid", False))
        overloaded = sample.get("input_overload") is True
        high = raw_valid and math.isfinite(utilization) and utilization >= 0.85
        if attempts >= 3:
            if overloaded or high or initial:
                raise RuntimeError(
                    "MFLI 自动量程重试 3 次仍过载或接近满量程，当前点未保存；"
                    "请检查输入信号及接线"
                )
            return sample
        if overloaded:
            action = self._run_mfli_autorange("STFMR input overload")
        elif initial:
            action = self._run_mfli_autorange("STFMR frequency start")
        elif raw_valid:
            action = self._auto_range_mfli(sample)
        else:
            # Unknown raw extrema must not cause down-ranging based on tiny R.
            action = ""
        if action:
            sample["auto_range"] = action
            sample["input_range_after_v"] = self.mfli_config["input_range_v"]
        return sample

    def disconnect_mfli(self) -> None:
        """Disable streaming and close only the Zurich LabOne session."""
        with self.lock:
            daq = self.mfli_daq
            bridge = self.mfli_bridge
            mfli_lease_resource = self.mfli_lease_resource
            if daq is None and bridge is None:
                self.mfli_lease_resource = ""
                release_instrument_lease(mfli_lease_resource, self)
                self._close_manager_if_unused()
                return
            if daq is not None:
                extref_demod = int(
                    self.mfli_config.get(
                        "extref_demodulator", NONLOCAL_MFLI_DEMODULATOR
                    )
                )
                disable_nodes = [
                    f"/{self.mfli_device}/extrefs/"
                    f"{NONLOCAL_MFLI_EXTREF_CHANNEL}/enable",
                    f"/{self.mfli_device}/demods/"
                    f"{NONLOCAL_MFLI_DEMODULATOR}/enable",
                ]
                if extref_demod != NONLOCAL_MFLI_DEMODULATOR:
                    disable_nodes.append(
                        f"/{self.mfli_device}/demods/{extref_demod}/enable"
                    )
                for node in disable_nodes:
                    try:
                        daq.setInt(node, 0)
                    except BaseException:
                        pass
                try:
                    daq.sync()
                except BaseException:
                    pass
            try:
                if daq is not None:
                    daq.disconnect()
            except BaseException:
                pass
            if bridge is not None:
                try:
                    bridge.close()
                except BaseException:
                    bridge.close(force=True)
            self.mfli_daq = None
            self.mfli_bridge = None
            self.mfli_device = ""
            self.mfli_props = {}
            self.mfli_config = {}
            self.mfli_identity = ""
            self.mfli_lease_resource = ""
            self.mfli_low_range_count = 0
            self.mfli_range_cooldown = 0
            self.mfli_initial_autorange_pending = False
            release_instrument_lease(mfli_lease_resource, self)
            self._close_manager_if_unused()
            self.logger("Non-local Zurich MFLI 已断开")



class STFMRScanRecorder:
    """Append-only, per-run files; called on the GUI acquisition path only.

    A point is flushed and fsynced before advancing current.  These files do
    not depend on the optional PNG/Pillow export or on a normal scan ending.
    """

    FIELDNAMES = (
        "index", "frequency_index", "loop", "point_in_loop", "elapsed_s",
        "meter", "current_a", "field_g", "x_v", "y_v", "voltage_v",
        "phase_deg", "reference_frequency_hz", "reference_valid",
        "microwave_frequency_ghz", "microwave_power_dbm",
        "modulation_frequency_hz", "mfli_timeconstant_s", "sr830_ref_in",
        "sr830_sensitivity_index", "sr830_sensitivity_full_scale_v",
        "sr830_auto_range_action", "timestamp_iso",
        "mfli_input_overload", "mfli_device_status_flags",
        "mfli_auto_range_enabled", "mfli_input_range_v",
        "mfli_input_utilization", "mfli_auto_range_action",
    )

    def __init__(self, parent: Path, settings: dict[str, object]) -> None:
        stamp = datetime_module.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        stem = f"STFMR_field_scan_{stamp}"
        self.directory = Path(parent) / stem
        self.csv_path = self.directory / f"{stem}.csv"
        self.log_path = self.directory / f"{stem}.log"
        self.csv_handle = None
        self.log_handle = None
        self.rows_written = 0
        self.closed = False
        # No exist_ok: a name collision must never overwrite another run.
        self.directory.mkdir()
        try:
            self.log_handle = self.log_path.open("x", encoding="utf-8")
            self.csv_handle = self.csv_path.open("x", newline="", encoding="utf-8-sig")
            self.writer = csv.DictWriter(self.csv_handle, fieldnames=self.FIELDNAMES)
            self.writer.writeheader()
            self._sync(self.csv_handle)
            self.event("run_prepared", csv_path=str(self.csv_path), settings=settings)
        except BaseException:
            # Retain any partially created files as evidence, but release all
            # handles.  The caller must not enable hardware after this failure.
            for handle in (self.csv_handle, self.log_handle):
                if handle is not None:
                    try:
                        handle.close()
                    except BaseException:
                        pass
            self.closed = True
            raise

    @staticmethod
    def _sync(handle) -> None:
        handle.flush()
        os.fsync(handle.fileno())

    def event(self, name: str, **details) -> None:
        if self.closed:
            raise RuntimeError("STFMR 日志已关闭")
        record = {
            "timestamp": datetime_module.datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "event": name,
            **details,
        }
        self.log_handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._sync(self.log_handle)

    def append_point(self, row: dict[str, object]) -> None:
        if self.closed:
            raise RuntimeError("STFMR 实时 CSV 已关闭")
        self.writer.writerow(row)
        self._sync(self.csv_handle)
        self.rows_written += 1

    def close(self, outcome: str, **details) -> list[str]:
        """Best-effort close; a bad log/disk must not skip the other handle."""
        if self.closed:
            return []
        errors = []
        try:
            self.event("run_finished", outcome=outcome, saved_points=self.rows_written, **details)
        except BaseException as error:
            errors.append(f"写入结束日志失败：{error}")
        self.closed = True
        for name, handle in (("CSV", self.csv_handle), ("log", self.log_handle)):
            if handle is not None:
                try:
                    self._sync(handle)
                except BaseException as error:
                    errors.append(f"刷新 {name} 失败：{error}")
                finally:
                    try:
                        handle.close()
                    except BaseException as error:
                        errors.append(f"关闭 {name} 失败：{error}")
        return errors

# ---- Fixed-field microwave/modulation grid application ----
import colorsys
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor

APP_NAME = 'Microwave_Transport_Test_NH_dev'


def parse_axis(value: str, limit: int = 10000) -> tuple[float, ...]:
    """Positive, unique values; comma lists or inclusive start:stop:step."""
    value = value.strip().replace('，', ',').replace('；', ',').replace(';', ',')
    try:
        if ':' in value:
            parts = value.split(':')
            if len(parts) != 3:
                raise ValueError('范围格式：起点:终点:步长')
            a, b, step = map(Decimal, parts)
            if not all(v.is_finite() for v in (a, b, step)) or step == 0 or (b-a)*step < 0:
                raise ValueError('范围必须有限，步长方向必须正确')
            count = int((b-a)/step) + 1
            if count > limit or count < 1:
                raise ValueError('单轴点数超限')
            values = [a+i*step for i in range(count)]
            if values[-1] != b:
                values.append(b)
        else:
            values = [Decimal(v.strip()) for v in value.split(',')]
        out = tuple(float(v) for v in values)
    except (InvalidOperation, OverflowError) as e:
        raise ValueError('请输入数值列表或起点:终点:步长') from e
    if not out or len(out) > limit or any(not math.isfinite(v) or v <= 0 for v in out):
        raise ValueError('频率必须为有限正数，单轴最多 10000 点')
    if len(set(out)) != len(out):
        raise ValueError('频率列表不能重复')
    return out


def prime_axis(start, stop):
    """Inclusive integer-Hz bounds, bounded sieve; no composite frequencies."""
    try:
        a, b = Decimal(str(start)), Decimal(str(stop))
        if not all(v.is_finite() and v == v.to_integral_value() for v in (a, b)):
            raise ValueError('调制起止频率须为整数 Hz')
        a, b = int(a), int(b)
    except (InvalidOperation, OverflowError):
        raise ValueError('调制起止频率须为整数 Hz')
    if not 1 <= a <= b <= 1000000:
        raise ValueError('调制范围须满足 1 ≤ 起点 ≤ 终点 ≤ 1000000 Hz')
    sieve = bytearray(b'\x01') * (b + 1)
    sieve[:2] = b'\x00\x00'
    for n in range(2, math.isqrt(b) + 1):
        if sieve[n]:sieve[n*n:b+1:n] = b'\x00' * ((b-n*n)//n+1)
    values = tuple(float(n) for n in range(a,b+1) if sieve[n])
    if not values:raise ValueError('此范围内没有质数，请调整起止频率')
    if len(values)>10000:raise ValueError('调制频率最多 10000 个质数，请缩小范围')
    return values


def lockin_indicators(sample):
    locked = sample.get('reference_valid')
    lock = ('green','已锁定') if locked is True else ('red','未锁定') if locked is False else ('gray','未知')
    if sample.get('auto_range'):
        return lock, ('amber','调整量程中')
    if 'status' in sample and 'sensitivity_index' in sample:
        overloaded = bool(int(sample['status']) & 0x07)
    else:overloaded = sample.get('input_overload')
    level = ('red','过载') if overloaded is True else ('green','量程正常') if overloaded is False else ('gray','量程未知')
    return lock, level


@dataclass(frozen=True)
class GridSettings:
    microwave_ghz: tuple[float, ...]
    modulation_hz: tuple[float, ...]
    fixed_current_a: float = 0.0
    voltage_limit_v: float = 10.0
    power_dbm: float = 18.0
    point_delay_s: float = 0.5
    field_settle_s: float = 0.3
    timeconstant_s: float = 0.1
    samples_per_cell: int = 3
    meter: str = 'MFLI'
    mfli_reference: str = 'Aux Input 1'
    sr830_reference: str = 'Sine'
    auto_range: bool = True
    simulation: bool = False

    def validate(self):
        for axis in (self.microwave_ghz, self.modulation_hz):
            if not axis or len(set(axis)) != len(axis) or any(not math.isfinite(v) or v <= 0 for v in axis):
                raise ValueError('频率必须是有限正数且不能重复')
        if len(self.microwave_ghz)*len(self.modulation_hz) > 20000:
            raise ValueError('二维网格最多 20000 格')
        if self.meter not in ('MFLI', 'SR830'):
            raise ValueError('请选择 MFLI 或 SR830')
        values = (self.fixed_current_a,self.voltage_limit_v,
                  self.power_dbm,self.point_delay_s,self.field_settle_s,self.timeconstant_s)
        if not all(math.isfinite(v) for v in values):
            raise ValueError('所有参数必须有限')
        if abs(self.fixed_current_a)>10:
            raise ValueError('固定电流必须在 ±10 A 内')
        if not 0 < self.voltage_limit_v <= 20 or not -120 <= self.power_dbm <= 25:
            raise ValueError('限压须在 0–20 V，功率须在 −120–25 dBm')
        if self.point_delay_s < 0.05 or self.field_settle_s < 0.1 or self.timeconstant_s <= 0:
            raise ValueError('点等待至少 0.05 s，电流设置后等待至少 0.1 s，时间常数须为正')
        if not isinstance(self.samples_per_cell,int) or not 1 <= self.samples_per_cell <= 1000:
            raise ValueError('每格采样数须为 1–1000 的整数')
        if self.mfli_reference not in NONLOCAL_MFLI_REFERENCE_INPUTS or self.sr830_reference not in STFMR_LOCKIN_SR830_REFERENCE_INPUTS:
            raise ValueError('参考输入选择无效')
        return self


def cell_settle_seconds(settings, modulation):
    tau = settings.timeconstant_s if settings.meter == 'MFLI' else 1.0
    return max(settings.point_delay_s, 10*tau, 5/modulation)


def estimated_scan_seconds(settings):
    """Scheduled waits only; hardware latency and range/lock retries are additional."""
    settings.validate()
    return 2*settings.field_settle_s + len(settings.microwave_ghz) * sum(
        cell_settle_seconds(settings, mod) + (settings.samples_per_cell + 1)*settings.point_delay_s
        for mod in settings.modulation_hz)


def format_duration(seconds):
    total = math.ceil(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return (f'{hours} 小时 ' if hours else '') + (f'{minutes} 分 ' if minutes or hours else '') + f'{secs} 秒'


class GridRecorder(STFMRScanRecorder):
    FIELDNAMES = ('index','microwave_index','modulation_index','sample_in_cell','elapsed_s',
                  'meter','microwave_frequency_ghz','modulation_frequency_hz','microwave_power_dbm',
                  'fixed_current_a','field_g','field_after_g','current_a','x_v','y_v','r_v','phase_deg',
                  'reference_frequency_hz','reference_valid','input_overload','input_range_v',
                  'input_utilization','device_status_flags','sensitivity_index','status','timestamp_iso')

    def __init__(self, parent, settings):
        # Same fsync/close semantics as the integrated recorder, separate naming.
        stamp = datetime_module.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        stem = f'{APP_NAME}_{stamp}'
        self.directory=Path(parent)/stem; self.directory.mkdir(parents=True,exist_ok=False)
        self.csv_path=self.directory/f'{stem}.csv'; self.log_path=self.directory/f'{stem}.log'
        self.csv_handle=self.log_handle=None; self.closed=False; self.rows_written=0
        try:
            self.csv_handle=self.csv_path.open('x',newline='',encoding='utf-8-sig')
            self.log_handle=self.log_path.open('x',encoding='utf-8')
            self.writer=csv.DictWriter(self.csv_handle,fieldnames=self.FIELDNAMES)
            self.writer.writeheader();self._sync(self.csv_handle)
            self.event('run_prepared',settings=settings,csv_path=str(self.csv_path))
        except BaseException:
            for h in (self.csv_handle,self.log_handle):
                if h: h.close()
            self.closed=True
            raise


class ScanStopped(Exception):
    pass


def component(sample, name):
    keys={'x':('x_v','X'),'y':('y_v','Y'),'r':('r_v','R','voltage')}[name]
    for k in keys:
        if k in sample and math.isfinite(float(sample[k])):return float(sample[k])
    raise RuntimeError(f'锁相回读缺少有效 {name.upper()}')


class GridEngine:
    """Worker sequences I/O; the isolated current reapply is acknowledged by Tk."""
    def __init__(self,devices,settings,recorder,stop_event,emit=lambda *a:None,main_thread_call=None):
        self.devices=devices;self.s=settings.validate();self.rec=recorder;self.stop=stop_event
        self.main_thread_call=main_thread_call or (lambda fn:fn())
        self.emit=emit;self.current=settings.fixed_current_a;self.cells={};self.started=time.monotonic()

    def check(self):
        if self.stop.is_set():raise ScanStopped()

    def wait(self,seconds):
        deadline=time.monotonic()+seconds
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:return
            if self.stop.wait(min(.5,remaining)):raise ScanStopped()
            # Monitoring only: never write current in response to the gauss meter.
            self.emit('field',{'field_g':self.field(),'current_a':self.current})

    def event(self,name,**kw):
        self.rec.event(name,**kw)
        self.emit('status',name)

    def field(self):
        self.check()
        b=STFMRGauss475Controller.value_in_gauss(self.devices['gauss'].read_field())
        if not math.isfinite(b):raise RuntimeError('LS475 返回无效磁场')
        return b

    def settle_seconds(self,modulation):
        return cell_settle_seconds(self.s,modulation)

    def sample(self,modulation,initial=False):
        attempts=0
        for read in range(35):
            self.check()
            sample=(self.devices['mfli'].read_stfmr_mfli(modulation,auto_range=self.s.auto_range,
                        initial=initial,attempts=attempts) if self.s.meter=='MFLI' else
                    self.devices['830'].read_sample(modulation))
            self.emit('readback',sample)
            action=str(sample.get('auto_range',''))
            if action:
                attempts+=1;initial=False
                self.event('autorange_retry',action=action,attempt=attempts,modulation_hz=modulation)
                if attempts>3:raise RuntimeError('量程调整超过 3 次，当前格不保存')
                self.wait(self.settle_seconds(modulation));continue
            if sample.get('input_overload') is True:
                raise RuntimeError('MFLI 输入过载，当前格不保存')
            ref=float(sample.get('reference_frequency_hz',math.nan))
            locked=bool(sample.get('reference_valid',False)) and math.isfinite(ref) and math.isclose(ref,modulation,rel_tol=.005,abs_tol=.1)
            if not locked:
                self.wait(max(.1, min(.5, self.s.point_delay_s)));continue
            if not bool(sample.get('measurement_valid',True)):
                raise RuntimeError('锁相输入状态无效，当前格不保存')
            for name in ('x','y','r'):component(sample,name)
            if not math.isfinite(float(sample.get('phase_deg',math.nan))):raise RuntimeError('无效锁相相位')
            return sample
        raise RuntimeError(f'参考未锁定到 {modulation:g} Hz，当前格不保存')

    def row(self,sample,b,after,mi,fi,k):
        return dict(index=self.rec.rows_written+1,microwave_index=fi,modulation_index=mi,
            sample_in_cell=k,elapsed_s=time.monotonic()-self.started,meter=self.s.meter,
            microwave_frequency_ghz=self.s.microwave_ghz[fi],modulation_frequency_hz=self.s.modulation_hz[mi],
            microwave_power_dbm=self.s.power_dbm,fixed_current_a=self.s.fixed_current_a,field_g=b,
            field_after_g=after,current_a=self.current,x_v=component(sample,'x'),y_v=component(sample,'y'),
            r_v=component(sample,'r'),phase_deg=float(sample['phase_deg']),
            reference_frequency_hz=float(sample['reference_frequency_hz']),reference_valid=True,
            input_overload=sample.get('input_overload'),input_range_v=sample.get('input_range_v'),
            input_utilization=sample.get('input_utilization'),device_status_flags=sample.get('device_status_flags'),
            sensitivity_index=sample.get('sensitivity_index'),status=sample.get('status',sample.get('lia_status')),
            timestamp_iso=datetime_module.datetime.now().astimezone().isoformat(timespec='milliseconds'))

    def run(self):
        outcome='completed';error=None;shutdown=[]
        try:
            self.check()
            before=self.field()
            self.rec.event('kepco_initialize_requested',current_setpoint_a=self.current,voltage_limit_v=self.s.voltage_limit_v,field_before_g=before)
            self.devices['kepco'].set_output('CURR',self.current,self.s.voltage_limit_v)
            self.wait(self.s.field_settle_s)
            initialized=self.field()
            self.rec.event('kepco_initialized',current_setpoint_a=self.current,field_g=initialized)
            # Match the integrated scan: a separate setpoint write on the Tk thread.
            # The worker must await acknowledgment before it may clean up outputs.
            def reapply():
                self.check()
                result=self.devices['kepco'].set_scan_current(self.current)
                return result,threading.current_thread().name
            self.check()
            self.rec.event('kepco_reapply_requested',current_setpoint_a=self.current)
            _,thread_name=self.main_thread_call(reapply)
            self.rec.event('kepco_reapplied',current_setpoint_a=self.current,thread=thread_name,readback_kind='command_cache_not_measured')
            self.wait(self.s.field_settle_s)
            after=self.field()
            self.rec.event('kepco_field_after_reapply',current_setpoint_a=self.current,field_before_g=before,field_initialized_g=initialized,field_after_g=after)
            self.emit('field',{'field_g':after,'current_a':self.current})
            s=self.s
            self.check()
            self.devices['smb'].configure(s.microwave_ghz[0],s.power_dbm,s.modulation_hz[0])
            if s.meter=='MFLI':
                self.devices['mfli'].configure_mfli('AC',1,'R',reference_source=s.mfli_reference,
                    sample_rate_hz=max(1.,1/max(s.point_delay_s,.05)),input_impedance='10 MΩ',
                    input_scaling=1.,timeconstant_s=s.timeconstant_s)
            else:self.devices['830'].configure(s.sr830_reference,s.auto_range)
            for mi,mod in enumerate(s.modulation_hz):
                for fi,carrier in enumerate(s.microwave_ghz):
                    self.check()
                    # Same trace-backed LFO command; no magnet reset between cells.
                    self.devices['smb'].set_frequency(carrier)
                    with self.devices['smb'].lock:
                        self.check()
                        self.devices['smb'].instrument.write(f'LFO:FREQ {mod:.7g} Hz')
                    self.event('cell_configured',microwave_index=fi,modulation_index=mi,
                               microwave_ghz=carrier,modulation_hz=mod)
                    self.wait(self.settle_seconds(mod))
                    # At every new coordinate discard two transitional VALID reads, not two grid cells.
                    self.sample(mod,initial=True);self.wait(s.point_delay_s);self.sample(mod)
                    collected=[]
                    while len(collected)<s.samples_per_cell:
                        self.wait(s.point_delay_s);b=self.field();sample=self.sample(mod);after=self.field()
                        self.emit('field',{'field_g':after,'current_a':self.current})
                        self.check();r=self.row(sample,b,after,mi,fi,len(collected)+1)
                        self.rec.append_point(r);collected.append(r)
                    meanx=sum(r['x_v'] for r in collected)/len(collected)
                    meany=sum(r['y_v'] for r in collected)/len(collected)
                    # Phase of the averaged complex voltage; no arithmetic averaging across ±180°.
                    theta=math.degrees(math.atan2(meany,meanx)) if math.hypot(meanx,meany)>1e-30 else math.nan
                    cell=dict(microwave_index=fi,modulation_index=mi,microwave_frequency_ghz=carrier,
                        modulation_frequency_hz=mod,x_v=meanx,y_v=meany,phase_deg=theta,
                        field_g=sum(r['field_g'] for r in collected)/len(collected),current_a=self.current,
                        samples=len(collected))
                    self.check();self.rec.event('cell_complete',**cell);self.cells[(mi,fi)]=cell
                    self.emit('cell',cell)
        except ScanStopped:outcome='stopped'
        except BaseException as exc:outcome='error';error=str(exc)
        finally:
            for key,fn in (('SMB100A',self.devices['smb'].rf_off),('KEPCO4886',self.devices['kepco'].zero_output)):
                try:fn()
                except BaseException as exc:shutdown.append(f'{key}: {exc}')
            if shutdown:outcome='error'
            export_errors=[]
            try:export_grid(self.rec.directory,self.s,self.cells)
            except BaseException as exc:export_errors.append(str(exc))
            close_errors=self.rec.close(outcome,error=error,shutdown_errors=shutdown,export_errors=export_errors,
                                        completed_cells=len(self.cells),outputs_off=not shutdown)
            result=dict(outcome=outcome,error=error,shutdown_errors=shutdown,export_errors=export_errors,
                        close_errors=close_errors,directory=str(self.rec.directory),completed_cells=len(self.cells))
            self.emit('finished',result)
        return result


def grid_values(settings,cells,key):
    return [[cells.get((j,i),{}).get(key,math.nan) for i in range(len(settings.microwave_ghz))]
            for j in range(len(settings.modulation_hz))]


def heatmap_scene(width,height,settings,cells,key):
    """Shared primitives for Tk and PNG: explicit missing cells, discrete parameter axes."""
    values=grid_values(settings,cells,key)
    finite=[v for row in values for v in row if math.isfinite(v)]
    limit=180. if key=='phase_deg' else max([abs(v)*1e6 for v in finite]+[1e-12])
    x0,y0,x1,y1=66,40,max(100,width-72),max(90,height-66)
    nx,ny=len(settings.microwave_ghz),len(settings.modulation_hz)
    shapes=[]
    def txt(x,y,text,size=10,anchor='center',color='#303640'):shapes.append(('text',x,y,str(text),size,anchor,color))
    def rect(a,b,c,d,color):shapes.append(('rect',a,b,c,d,color))
    def color(v):
        if not math.isfinite(v):return '#e4e7eb'
        if key=='phase_deg':rgb=colorsys.hsv_to_rgb((v+180)/360,.76,.86)
        else:
            t=max(-1.,min(1.,v*1e6/limit))
            rgb=(1-.78*max(-t,0),1-.70*abs(t),1-.78*max(t,0))
        return '#'+''.join(f'{round(c*255):02x}' for c in rgb)
    rect(x0-1,y0-1,x1+1,y1+1,'#303640')
    for j,row in enumerate(values):
        for i,v in enumerate(row):
            rect(x0+i*(x1-x0)/nx,y1-(j+1)*(y1-y0)/ny,x0+(i+1)*(x1-x0)/nx,y1-j*(y1-y0)/ny,color(v))
    titles={'x_v':'X (µV)','y_v':'Y (µV)','phase_deg':'Theta (°)'}
    txt(width/2,18,titles[key],12)
    for i,v in enumerate(settings.microwave_ghz):
        if i%max(1,math.ceil(nx/6))==0 or i==nx-1:txt(x0+(i+.5)*(x1-x0)/nx,y1+14,f'{v:g}',9)
    for j,v in enumerate(settings.modulation_hz):
        if j%max(1,math.ceil(ny/8))==0 or j==ny-1:txt(x0-5,y1-(j+.5)*(y1-y0)/ny,f'{v:g}',9,'e')
    txt((x0+x1)/2,height-17,'Microwave frequency (GHz)',10)
    txt(x0,y0-13,'Modulation (Hz)',9,'w')
    for k in range(100):
        v=-limit+2*limit*k/99
        rect(x1+12,y1-(k+1)*(y1-y0)/100,x1+24,y1-k*(y1-y0)/100,color(v if key=='phase_deg' else v/1e6))
    color_ticks=(-1,-.5,0,.5,1) if key=='phase_deg' else (-1,0,1)
    for t in color_ticks:txt(x1+28,y1-(t+1)/2*(y1-y0),f'{t*limit:.3g}',8,'w')
    return shapes,(x0,y0,x1,y1)


def export_grid(directory,settings,cells):
    directory=Path(directory)
    for key,label in (('x_v','X_V'),('y_v','Y_V'),('phase_deg','Theta_deg')):
        with (directory/f'{label}_matrix.csv').open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f);w.writerow(['modulation_Hz / microwave_GHz',*settings.microwave_ghz])
            for mod,row in zip(settings.modulation_hz,grid_values(settings,cells,key)):
                w.writerow([mod,*[v if math.isfinite(v) else '' for v in row]])
    with (directory/'cells.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=('microwave_index','modulation_index','microwave_frequency_ghz','modulation_frequency_hz',
                'x_v','y_v','phase_deg','field_g','current_a','samples')
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(cells.values())
    (directory/'settings.json').write_text(json.dumps(asdict(settings),ensure_ascii=False,indent=2))
    if Image is None:raise RuntimeError('矩阵 CSV 已保存；缺少 Pillow，未生成 PNG')
    from PIL import ImageDraw,ImageFont
    scale=6;w,h=700,450
    for key,label in (('x_v','X'),('y_v','Y'),('phase_deg','Theta')):
        im=Image.new('RGB',(w*scale,h*scale),'white');draw=ImageDraw.Draw(im);fonts={}
        for primitive in heatmap_scene(w,h,settings,cells,key)[0]:
            if primitive[0]=='rect':
                _,a,b,c,d,color=primitive;draw.rectangle((round(a*scale),round(b*scale),round(c*scale),round(d*scale)),fill=color)
            else:
                _,x,y,text,size,anchor,color=primitive
                if size not in fonts:
                    for candidate in ('DejaVuSans.ttf','Arial.ttf','C:/Windows/Fonts/arial.ttf','/System/Library/Fonts/Supplemental/Arial.ttf'):
                        try:fonts[size]=ImageFont.truetype(candidate,round(size*scale));break
                        except OSError:pass
                    else:fonts[size]=ImageFont.load_default()
                draw.text((x*scale,y*scale),text,font=fonts[size],fill=color,anchor={'center':'mm','e':'rm','w':'lm'}[anchor])
        im.save(directory/f'{label}_heatmap.png',dpi=(900,900))


class DemoDevice:
    """Explicit simulation only; never opens a VISA/LabOne session."""
    def __init__(self,bank,key):self.bank=bank;self.key=key;self.connected=False;self.lock=threading.RLock();self.instrument=self
    @property
    def mfli_connected(self):return self.connected
    def connect(self,resource):self.connected=True;return 'SIMULATION '+resource
    connect_mfli=connect
    def disconnect(self):self.connected=False
    disconnect_mfli=disconnect
    def configure(self,*args):
        if self.key=='smb':self.bank.update(freq=args[0],mod=args[2],rf=True)
    def configure_mfli(self,*a,**k):pass
    def set_output(self,mode,current,limit):self.bank['current']=current
    def set_scan_current(self,current):self.bank['current']=current
    def zero_output(self):self.bank['current']=0
    def rf_off(self):self.bank['rf']=False
    def set_frequency(self,f):self.bank['freq']=f
    def write(self,command):
        if command.startswith('LFO:FREQ'):self.bank['mod']=float(command.split()[1])
    def read_field(self):return {'field':300*self.bank['current'],'unit':'G','unit_code':1}
    def read_sample(self,expected):
        f=self.bank.get('freq',1.);m=self.bank.get('mod',17.3);amp=2e-6*math.exp(-((f-2.0)/.6)**2)+1e-7
        angle=math.atan(m/80)+f/3;x=amp*math.cos(angle);y=amp*math.sin(angle)
        return {'X':x,'Y':y,'R':amp,'phase_deg':math.degrees(angle),'reference_valid':True,
                'reference_frequency_hz':expected,'input_overload':False,'input_range_v':.003}
    def read_stfmr_mfli(self,expected,**kw):return self.read_sample(expected)


class MicrowaveTransportApp:
    def __init__(self,root,demo=False):
        self.root=root;self.demo=demo;self.running=False;self.busy=False;self.closing=False
        self.events=queue.Queue();self.executor=ThreadPoolExecutor(max_workers=1);self.stop_event=threading.Event()
        self.cells={};self.settings=None;self.last_directory=None;self.dirty=False;self.idle_field_pending=False;self.last_idle_field_time=0.
        self.root.title(APP_NAME+(' — SIMULATION' if demo else ''));self.root.geometry('1360x860');self.root.minsize(1000,700)
        configure_tk_font_system(root)
        logger=lambda message:self.events.put(('log',str(message)))
        if demo:
            bank={'current':0.,'rf':False};self.devices={k:DemoDevice(bank,k) for k in STFMR_LOCKIN_DEFAULT_RESOURCES}
        else:
            self.devices={'kepco':STFMRKepco4886Controller(logger),'gauss':STFMRGauss475Controller(logger),
                          'smb':STFMRSMB100AController(logger),'830':STFMRSR830Controller(logger),'mfli':NonLocalACController(logger)}
        self.resources={k:tk.StringVar(value=v) for k,v in STFMR_LOCKIN_DEFAULT_RESOURCES.items()}
        self.connection_labels={};self.connection_lamps={};self.connection_buttons=[];self.editable=[]
        self.vars={k:tk.StringVar(value=v) for k,v in {
            'microwave':'1:3:0.1','mod_start':'5','mod_stop':'400','current':'0','limit':'10','power':'18','delay':'0.5','fieldsettle':'0.3','tau':'0.1',
            'samples':'3','meter':'MFLI','mfli_ref':'Aux Input 1','sr_ref':'Sine'}.items()}
        self.autorange=tk.BooleanVar(value=True);self.safety=tk.BooleanVar(value=False)
        self.status=tk.StringVar(value='就绪。先连接仪器，再设置固定电流及二维参数。')
        self.readback=tk.StringVar(value='B: — G     I: — A     X: — V     Y: — V     Theta: —°')
        self._build();self.root.protocol('WM_DELETE_WINDOW',self.close);self.root.after(80,self.poll)

    def _build(self):
        outer=ttk.Frame(self.root,padding=8);outer.pack(fill='both',expand=True)
        instruments=ttk.LabelFrame(outer,text='STFMR 仪器连接'+('（模拟）' if self.demo else ''),padding=5);instruments.pack(fill='x')
        for n,(key,name) in enumerate((('kepco','KEPCO4886'),('830','SR830'),('gauss','LS475'),('mfli','MFLI'),('smb','SMB100A'))):
            frame=ttk.Frame(instruments);frame.grid(row=n//2,column=n%2,sticky='ew',padx=4,pady=2);instruments.columnconfigure(n%2,weight=1)
            ttk.Label(frame,text=name,width=12).pack(side='left');e=ttk.Entry(frame,textvariable=self.resources[key],width=24);e.pack(side='left',fill='x',expand=True);self.editable.append((e,'normal'))
            for action,label in (('connect','连接'),('disconnect','断开')):
                b=ttk.Button(frame,text=label,width=5,command=lambda k=key,a=action:self.connection(k,a));b.pack(side='left',padx=2);self.connection_buttons.append(b)
            self.connection_lamps[key]=self.make_lamp(frame, '未连接')
            self.connection_labels[key]=self.connection_lamps[key][2]
        params=ttk.LabelFrame(outer,text='固定电流 · 微波频率 × 调制频率',padding=6);params.pack(fill='x',pady=5)
        specs=[('微波 GHz（列表/起:止:步）','microwave'),('调制起点 Hz（质数）','mod_start'),('调制终点 Hz（质数）','mod_stop'),('功率 dBm','power'),
               ('固定电流 A','current'),('限压 V','limit'),
               ('每点等待 s','delay'),('电流设置后等待 s','fieldsettle'),('每格采样数','samples'),('MFLI 时间常数 s','tau')]
        for n,(label,key) in enumerate(specs):
            frame=ttk.Frame(params);frame.grid(row=n//4,column=n%4,sticky='ew',padx=4,pady=3);params.columnconfigure(n%4,weight=1)
            ttk.Label(frame,text=label).pack(anchor='w');e=ttk.Entry(frame,textvariable=self.vars[key],width=26 if n<2 else 12);e.pack(fill='x');self.editable.append((e,'normal'))
        frame=ttk.Frame(params);frame.grid(row=3,column=0,columnspan=4,sticky='ew')
        for label,key,values in [('读取','meter',('MFLI','SR830')),('MFLI REF','mfli_ref',tuple(NONLOCAL_MFLI_REFERENCE_INPUTS)),('SR830 REF','sr_ref',tuple(STFMR_LOCKIN_SR830_REFERENCE_INPUTS))]:
            ttk.Label(frame,text=label).pack(side='left',padx=4);e=ttk.Combobox(frame,textvariable=self.vars[key],values=values,state='readonly',width=12);e.pack(side='left');self.editable.append((e,'readonly'))
        a=ttk.Checkbutton(frame,text='锁相自动量程',variable=self.autorange);a.pack(side='left',padx=10);self.editable.append((a,'normal'))
        a=ttk.Checkbutton(params,text='已确认磁体极性、冷却、接线、输出上限及人员安全',variable=self.safety);a.grid(row=4,column=0,columnspan=4,sticky='w',pady=4);self.editable.append((a,'normal'))
        actions=ttk.Frame(outer);actions.pack(fill='x')
        self.start_button=ttk.Button(actions,text='开始二维扫描',command=self.start);self.start_button.pack(side='left',padx=3)
        self.stop_button=ttk.Button(actions,text='停止 / 输出归零',command=self.stop,state='disabled');self.stop_button.pack(side='left',padx=3)
        self.zero_button=ttk.Button(actions,text='归零并关闭输出',command=self.zero);self.zero_button.pack(side='left',padx=3)
        self.export_button=ttk.Button(actions,text='导出当前热力图',command=self.export,state='disabled');self.export_button.pack(side='left',padx=3)
        self.progress=ttk.Progressbar(actions,mode='determinate');self.progress.pack(side='left',fill='x',expand=True,padx=10)
        lights=ttk.Frame(outer);lights.pack(fill='x',pady=3)
        ttk.Label(lights,text='锁相参考：').pack(side='left');self.lock_lamp=self.make_lamp(lights,'待读取')
        ttk.Label(lights,text='   输入量程：').pack(side='left');self.range_lamp=self.make_lamp(lights,'待读取')
        self.prime_info=tk.StringVar();ttk.Label(lights,textvariable=self.prime_info).pack(side='right')
        for key in ('mod_start','mod_stop'):self.vars[key].trace_add('write',lambda *args:self.update_prime_info())
        self.vars['meter'].trace_add('write',lambda *args:self.reset_lock_lamps())
        self.update_prime_info()
        ttk.Label(outer,textvariable=self.readback).pack(fill='x',pady=3)
        ttk.Label(outer,textvariable=self.status,wraplength=1250).pack(fill='x',pady=3)
        plots=ttk.Frame(outer);plots.pack(fill='both',expand=True);self.canvases={}
        for i,key in enumerate(('x_v','y_v','phase_deg')):
            c=tk.Canvas(plots,bg='white',highlightthickness=1,highlightbackground='#cad0d8');c.grid(row=0,column=i,sticky='nsew',padx=3);plots.columnconfigure(i,weight=1,uniform='heatmap');c.bind('<Configure>',lambda e:self.mark_dirty());c.bind('<Button-1>',lambda e,k=key:self.cell_click(e,k));self.canvases[key]=c
        plots.rowconfigure(0,weight=1)
        ttk.Label(outer,text='灰格：未完成采集；X/Y 为各格均值，Theta 为均值复电压的相位。点击色块查看实测值。').pack(anchor='w')
        self.log_widget=tk.Text(outer,height=4,state='disabled',font=('TkFixedFont',9));self.log_widget.pack(fill='x',pady=(4,0))

    def make_lamp(self, parent, label):
        canvas=tk.Canvas(parent,width=16,height=16,highlightthickness=0)
        canvas.pack(side='left',padx=(4,1))
        dot=canvas.create_oval(3,3,13,13,fill='#9ca3af',outline='')
        text=tk.StringVar(value=label);ttk.Label(parent,textvariable=text,width=11).pack(side='left')
        return canvas,dot,text

    def set_lamp(self, lamp, color, label):
        canvas,dot,text=lamp
        canvas.itemconfigure(dot,fill={'green':'#16a34a','red':'#dc2626','amber':'#d97706','gray':'#9ca3af'}[color]);text.set(label)

    def reset_lock_lamps(self):
        for lamp in (self.lock_lamp,self.range_lamp):self.set_lamp(lamp,'gray','待读取')

    def update_prime_info(self):
        try:
            values=prime_axis(self.vars['mod_start'].get(),self.vars['mod_stop'].get())
            self.prime_info.set(f'调制：{len(values)} 个质数，{values[0]:g}–{values[-1]:g} Hz')
        except ValueError as exc:self.prime_info.set(str(exc))

    def mark_dirty(self):self.dirty=True
    def connected(self,key):return self.devices[key].mfli_connected if key=='mfli' else self.devices[key].connected
    def emit(self,*event):self.events.put(event)
    def busy_ui(self,busy):
        self.busy=busy
        for w,state in self.editable:w.configure(state='disabled' if busy else state)
        for w in self.connection_buttons:w.configure(state='disabled' if busy else 'normal')
        self.start_button.configure(state='disabled' if busy else 'normal');self.zero_button.configure(state='disabled' if busy else 'normal')
        self.stop_button.configure(state='normal' if self.running else 'disabled');self.export_button.configure(state='normal' if self.settings and not busy else 'disabled')

    def submit(self,fn):
        future=self.executor.submit(fn)
        def done(f):
            try:result=f.result();self.emit('operation_done',result)
            except BaseException as exc:self.emit('operation_error',str(exc))
        future.add_done_callback(done)

    def connection(self,key,action):
        if self.busy or self.closing:return
        if action=='connect' and self.connected(key):return
        if action=='disconnect' and not self.connected(key):return
        self.busy_ui(True);device=self.devices[key];resource=self.resources[key].get().strip()
        method=getattr(device,('connect_mfli' if key=='mfli' else 'connect') if action=='connect' else ('disconnect_mfli' if key=='mfli' else 'disconnect'))
        fn=(lambda:method(resource)) if action=='connect' else method
        # Preserve the integrated driver's main-thread-only KEPCO session opening.
        if key=='kepco' and action=='connect':
            try:self.emit('operation_done',fn())
            except BaseException as exc:self.emit('operation_error',str(exc))
        else:self.submit(fn)

    def inputs(self):
        v=lambda k:float(self.vars[k].get())
        return GridSettings(parse_axis(self.vars['microwave'].get()),prime_axis(self.vars['mod_start'].get(),self.vars['mod_stop'].get()),
            fixed_current_a=v('current'),voltage_limit_v=v('limit'),
            power_dbm=v('power'),point_delay_s=v('delay'),field_settle_s=v('fieldsettle'),timeconstant_s=v('tau'),
            samples_per_cell=int(self.vars['samples'].get()),meter=self.vars['meter'].get(),mfli_reference=self.vars['mfli_ref'].get(),
            sr830_reference=self.vars['sr_ref'].get(),auto_range=self.autorange.get(),simulation=self.demo).validate()

    def start(self):
        if self.busy or self.closing:return
        try:
            settings=self.inputs()
            if not self.safety.get():raise ValueError('请先勾选接线、磁体冷却及输出安全确认')
            for key in ('kepco','gauss','smb','mfli' if settings.meter=='MFLI' else '830'):
                if not self.connected(key):raise ValueError(f'{key} 尚未连接')
        except Exception as exc:messagebox.showerror(APP_NAME,str(exc),parent=self.root);return
        total=len(settings.microwave_ghz)*len(settings.modulation_hz)
        if not messagebox.askyesno('开始固定电流二维扫描',f'固定电流 {settings.fixed_current_a:+g} A（LS475 仅监测，无反馈）\n微波 {len(settings.microwave_ghz)} 点 × 调制 {len(settings.modulation_hz)} 点 = {total} 格\n功率 {settings.power_dbm:g} dBm；限压 {settings.voltage_limit_v:g} V\n每格保存 {settings.samples_per_cell} 次采样，切换后先丢弃两次过渡读取。\n预计用时（等待时间基准）：{format_duration(estimated_scan_seconds(settings))}\n实际另加仪器通信、配置、自动量程/锁定重试及文件保存时间。\n完成、停止和异常均关闭 RF 并归零电流。',parent=self.root):return
        parent=filedialog.askdirectory(title='选择本次数据保存位置',parent=self.root)
        if not parent:return
        try:rec=GridRecorder(parent,dict(asdict(settings),resources={k:v.get() for k,v in self.resources.items()},program=APP_NAME))
        except Exception as exc:messagebox.showerror('创建记录失败',str(exc),parent=self.root);return
        self.reset_lock_lamps();self.settings=settings;self.cells={};self.last_directory=rec.directory;self.running=True;self.stop_event.clear();self.busy_ui(True)
        self.progress.configure(maximum=total,value=0);self.mark_dirty()
        engine=GridEngine(self.devices,settings,rec,self.stop_event,self.emit,self.call_on_main_thread)
        try:self.executor.submit(engine.run)
        except BaseException as exc:
            rec.close('error',error=str(exc));self.running=False;self.busy_ui(False);self.status.set(str(exc))

    def call_on_main_thread(self, fn):
        if threading.current_thread() is threading.main_thread():return fn()
        done=threading.Event();box={}
        self.emit('main_thread_call',(fn,done,box))
        # Do not return on stop before the UI acknowledges the queued command:
        # otherwise a delayed write could run after worker shutdown.
        done.wait()
        if 'error' in box:raise box['error']
        return box['result']

    def stop(self):
        if self.running:self.stop_event.set();self.status.set('正在停止，等待当前仪器调用结束后关闭输出…')
    def zero(self):
        if self.busy:return
        self.busy_ui(True)
        def operation():
            errors=[]
            for fn in (self.devices['smb'].rf_off,self.devices['kepco'].zero_output):
                try:fn()
                except BaseException as exc:errors.append(str(exc))
            if errors:raise RuntimeError('; '.join(errors))
            return 'RF 已关闭，电流已归零'
        self.submit(operation)

    def export(self):
        if self.busy or not self.settings:return
        parent=filedialog.askdirectory(title='导出热力图',parent=self.root)
        if not parent:return
        out=Path(parent)/(APP_NAME+'_export_'+datetime_module.datetime.now().strftime('%Y%m%d_%H%M%S_%f'));out.mkdir()
        settings=self.settings;cells=dict(self.cells);self.busy_ui(True);self.submit(lambda:export_grid(out,settings,cells))

    def draw(self):
        for key,c in self.canvases.items():
            c.delete('all')
            if self.settings is None:
                c.create_text(c.winfo_width()/2,c.winfo_height()/2,text={'x_v':'X','y_v':'Y','phase_deg':'Theta'}[key]+'\n等待二维扫描',fill='#777777');continue
            shapes,_=heatmap_scene(c.winfo_width(),c.winfo_height(),self.settings,self.cells,key)
            for primitive in shapes:
                if primitive[0]=='rect':_,a,b,d,e,col=primitive;c.create_rectangle(a,b,d,e,fill=col,outline='')
                else:
                    _,x,y,t,size,anchor,col=primitive;c.create_text(x,y,text=t,font=('Arial',size),anchor=anchor,fill=col)
        self.dirty=False

    def cell_click(self,event,key):
        if self.settings is None:return
        c=self.canvases[key];_,(x0,y0,x1,y1)=heatmap_scene(c.winfo_width(),c.winfo_height(),self.settings,self.cells,key)
        if not(x0<=event.x<x1 and y0<=event.y<y1):return
        i=int((event.x-x0)/(x1-x0)*len(self.settings.microwave_ghz));j=int((y1-event.y)/(y1-y0)*len(self.settings.modulation_hz))
        cell=self.cells.get((j,i))
        self.status.set(f'{self.settings.microwave_ghz[i]:g} GHz / {self.settings.modulation_hz[j]:g} Hz：'+
            (f"X={cell['x_v']:.6g} V，Y={cell['y_v']:.6g} V，Theta={cell['phase_deg']:.4g}°，B={cell['field_g']:.5g} G，n={cell['samples']}" if cell else '未完成采集'))

    def poll(self):
        for _ in range(250):
            try:kind,data=self.events.get_nowait()
            except queue.Empty:break
            if kind=='main_thread_call':
                fn,done,box=data
                try:box['result']=fn()
                except BaseException as exc:box['error']=exc
                finally:done.set()
            elif kind=='log':
                self.log_widget.configure(state='normal');self.log_widget.insert('end',str(data)+'\n');self.log_widget.see('end');self.log_widget.configure(state='disabled')
            elif kind=='status':
                self.status.set(str(data))
                if data=='cell_configured':
                    self.set_lamp(self.lock_lamp,'amber','等待锁定');self.set_lamp(self.range_lamp,'gray','待读取')
            elif kind=='field':self.readback.set(f"B: {data['field_g']:+.6g} G     I(set): {data['current_a']:+.6g} A" + ("     X:" + self.readback.get().split("     X:",1)[1] if "     X:" in self.readback.get() else ""))
            elif kind=='readback':
                lock,level=lockin_indicators(data)
                self.set_lamp(self.lock_lamp,*lock);self.set_lamp(self.range_lamp,*level)
                try:self.readback.set(self.readback.get().split('     X:')[0]+f"     X: {component(data,'x'):+.6g} V     Y: {component(data,'y'):+.6g} V     Theta: {float(data['phase_deg']):+.5g}°     REF: {'LOCK' if data.get('reference_valid') else '等待'}     Overload: {data.get('input_overload','—')}")
                except Exception:pass
            elif kind=='cell':
                self.cells[(data['modulation_index'],data['microwave_index'])]=data;self.progress.configure(value=len(self.cells));self.mark_dirty()
                self.status.set(f"完成 {len(self.cells)}/{int(float(self.progress['maximum']))} 格；{data['microwave_frequency_ghz']:g} GHz / {data['modulation_frequency_hz']:g} Hz")
            elif kind=='finished':
                self.running=False;self.busy_ui(False);self.reset_lock_lamps()
                issues=[data.get('error')]+data.get('shutdown_errors',[])+data.get('export_errors',[])+data.get('close_errors',[])
                self.status.set(f"{data['outcome']}；完成 {data['completed_cells']} 格；保存于 {data['directory']}"+('；'+'；'.join(str(v) for v in issues if v) if any(issues) else ''))
                if any(issues):messagebox.showerror('扫描结果 / 输出状态',self.status.get(),parent=self.root)
            elif kind in ('operation_done','operation_error'):
                self.busy_ui(False)
                for key,lamp in self.connection_lamps.items():self.set_lamp(lamp,'green' if self.connected(key) else 'gray','已连接' if self.connected(key) else '未连接')
                if not self.connected('mfli' if self.vars['meter'].get()=='MFLI' else '830'):self.reset_lock_lamps()
                if kind=='operation_error':messagebox.showerror(APP_NAME,str(data),parent=self.root)
                elif data:self.status.set(str(data))
            elif kind=='idle_field':
                self.idle_field_pending=False
                if data.get('error'):
                    self.status.set('LS475 实时读取失败：'+data['error'])
                elif not self.running:
                    self.readback.set(f"B: {data['field_g']:+.6g} G（LS475 实时监测，无电流反馈）")
            elif kind=='closed':
                if data:messagebox.showerror('关闭仪器',str(data),parent=self.root)
                self.executor.shutdown(wait=False);self.root.destroy();return
        if not self.busy and not self.closing and not self.idle_field_pending and self.connected('gauss') and time.monotonic()-self.last_idle_field_time>=1:
            self.idle_field_pending=True;self.last_idle_field_time=time.monotonic()
            def read_idle_field():
                try:
                    value=STFMRGauss475Controller.value_in_gauss(self.devices['gauss'].read_field())
                    self.emit('idle_field',{'field_g':value})
                except BaseException as exc:self.emit('idle_field',{'error':str(exc)})
            self.executor.submit(read_idle_field)
        if self.dirty:self.draw()
        if self.closing and not self.busy and not self.running:
            self.busy_ui(True)
            def disconnect_all():
                errors=[]
                for key,device in self.devices.items():
                    try:(device.disconnect_mfli if key=='mfli' else device.disconnect)()
                    except BaseException as exc:errors.append(f'{key}: {exc}')
                self.emit('closed','; '.join(errors))
            self.executor.submit(disconnect_all)
        self.root.after(100,self.poll)

    def close(self):
        if self.closing:return
        self.closing=True;self.stop();self.status.set('正在关闭输出和仪器连接…')


def main():
    root=tk.Tk();MicrowaveTransportApp(root,demo='--demo' in sys.argv);root.mainloop()


if __name__=='__main__':main()
