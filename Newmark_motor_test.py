"""Newmark NSC-A1 rotation stage: a standalone test over USB (user, 2026-10-13).

The stage is a Newmark RM-5-110 (72:1 worm gear, 1.8 deg stepper, home
switch, no encoder, no limit switches, continuous 360 deg, at most 25 deg/s)
on an NSC-A1 controller (a rebadged Arcus ACE-SDE).  It turns the sample
holder, wires attached, inside the RT-E electromagnet.  Commands follow the
NSC-A1 manual rev 1.3; nothing here has been tried on the hardware yet.

Needs only 64-bit Python 3.11 on Windows (standard library) and the vendor's
USB layer: the Performax USB driver (Performax USB Setup 4.01.3) and the
64-bit PerformaxCom.dll + SiUSBXp.dll (Arcus "USB 64-bit DLL v2.06"), side
by side in this folder or in --dll-dir.  No COM port is ever opened.

Usage (from a terminal in this folder):

    py Newmark_motor_test.py                       read only: devices, identity, status, position,
                                                   speeds, settings, driver (RR), steps/deg
    py Newmark_motor_test.py move --degrees 10     turn +10 deg at 5 deg/s (asks first)
    py Newmark_motor_test.py move --degrees -10 --speed 2
    py Newmark_motor_test.py stop                  STOP (decelerated), wait, say where it stopped
    py Newmark_motor_test.py zero                  call this position 0 deg (PX=0; asks first; lost at
                                                   power-off)
    py Newmark_motor_test.py clear                 clear a latched limit error (CLR)
    py Newmark_motor_test.py enable                motor driver on (EO=1; asks first)
    py Newmark_motor_test.py disable               motor driver off (EO=0)
    py Newmark_motor_test.py home --direction +    find the home switch (HL+; asks first; only where the
                                                   whole search stays within +/-360 deg of the zero)
    py Newmark_motor_test.py move --degrees 10 --dry-run
                                                   the same on a simulated controller

    --serial S    the controller's USB serial (needed only with several Performax devices)
    --dll-dir D   the folder with the 64-bit PerformaxCom.dll and SiUSBXp.dll
    --speed S     deg/s for move and home (default 5, at most 20)
    --yes         do not ask
    --dry-run     a simulated controller: prints every command, sends nothing

Every command sent and every reply is printed ("> " sent, "< " received);
while the stage turns, the status and position polls are summarized once a
second.  Only the commands this script needs can be sent (see ALLOWED
below): anything else (STORE, RW, J+, POL=, ...) is refused before it
reaches the controller, and nothing is ever stored to flash.

Exit status: 0 done; 1 refused, not confirmed, or failed with the motor
confirmed stopped; 2 a usage error, such as a --dll-dir without
PerformaxCom.dll (nothing sent); 3 the motion state is unknown: check the
stage.

SAFETY
  - Sample wires: every target stays within +/-360 deg of the zero (PX = 0),
    and moves are absolute, so 350 -> 0 goes back -350 deg, never across
    360.  Before the first move, set the zero where the wires hang free
    ("zero", which asks).  The zero is lost when the controller is powered
    off.
  - "home" turns at most one turn + 10 deg to find the switch: PX is read
    at every poll, and it is stopped (STOP) if the counter has not been
    reset at a switch by then (no switch signal), or if it runs on more
    than the slowing down + HCA + 10 deg after it; a time-out backs this
    up.  It is refused unless all of that stays within +/-360 deg of the
    zero (from the zero itself, first move 13 deg or more the other way at
    5 deg/s: the refusal says how far).  Afterwards the zero, and with it
    the +/-360 deg limit, is the home switch: the script says where the old
    zero now is, so you can turn back there and run "zero" again.  It says
    so too when a search is stopped or fails after the counter reset.
  - Ctrl+C at any point sends STOP if a move may be running, waits until it
    has stopped (ABORT if STOP does not stop it) and says where it stopped.
    While it stops and reports, a further Ctrl+C only asks for ABORT.
  - A move counts as ended after three status reads in a row that say it is
    not moving, with PX where it should be; a stop, after three such reads
    with PX unchanged.
  - At most 20 deg/s (the stage allows 25 deg/s).
  - A move is refused while it moves, with a latched limit error, with a
    limit input on (the RM-5-110 has no limit switches: that is a polarity
    problem, fix it in Newmark's QuickMotion), or with the driver off (EO=0).
  - Driver parameters, polarity, the device name and anything stored to
    flash are never written; QuickMotion is the place for those.
"""
import argparse
import ctypes
import math
import os
import re
import signal
import sys
import time

# ---- the vendor DLL ----------------------------------------------------------
DLL_NAME = "PerformaxCom.dll"
SIUSB_NAME = "SiUSBXp.dll"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BUFFER_SIZE = 64            # fnPerformaxComSendRecv writes and reads exactly 64 bytes (manual, ch. 7)
PRODUCT_STRING_SIZE = 256
TIMEOUT_MS = 1000           # the manual's recommended read/write timeout
SERIAL_OPTION, DESCRIPTION_OPTION = 0, 1
DWORD = ctypes.c_uint32     # a Windows DWORD is 32 bits on every Windows
HANDLE = ctypes.c_void_p
BOOL = ctypes.c_int
FLUSH = "fnPerformaxComFlush"
# BOOL fn...(...) as in the manual; fnPerformaxComFlush exists only in newer DLLs.
SIGNATURES = (
    ("fnPerformaxComGetNumDevices", [ctypes.POINTER(DWORD)]),
    ("fnPerformaxComGetProductString", [DWORD, ctypes.c_void_p, DWORD]),
    ("fnPerformaxComOpen", [DWORD, ctypes.POINTER(HANDLE)]),
    ("fnPerformaxComClose", [HANDLE]),
    ("fnPerformaxComSetTimeouts", [DWORD, DWORD]),
    ("fnPerformaxComSendRecv", [HANDLE, ctypes.c_void_p, DWORD, DWORD, ctypes.c_void_p]),
    (FLUSH, [HANDLE]),
)
_DLL_DIRECTORIES = []       # os.add_dll_directory cookies, kept for the life of the program

# ---- the stage ---------------------------------------------------------------
FULL_STEPS_PER_TURN = 200   # 1.8 deg motor (Lin 5718M-05P)
GEAR_RATIO = 72             # RM-5-110 worm gear
FULL_STEPS_PER_DEG = FULL_STEPS_PER_TURN * GEAR_RATIO // 360    # 40; x DRVMS micro-steps
DEFAULT_SPEED = 5.0         # deg/s
MIN_SPEED, MAX_SPEED = 0.1, 20.0
ACC_MS = 300
MIN_LSPD = 10               # pps; the manual's smallest low speed at these high speeds (Table A.0)
MAX_ACC_MS = 5000           # the allowlist's ceiling for ACC= (the script sends 300)
MAX_DRVMS = 500             # the driver's finest micro-stepping (manual: DRVMS 2 - 500)
MAX_MOVE_DEG = 360.0        # one relative move
CABLE_LIMIT_DEG = 360.0     # every target within +/-360 deg of the zero (sample wires)
PX_LIMIT = 2 ** 27 - 1      # PX is a 28-bit signed number
PX_SAFE = PX_LIMIT // 4     # targets stay far inside it
POLL_S = 0.05
IDLE_READS = 3              # MST must say "not moving" this many polls in a row before a move counts as ended
SETTLE_S = 1.0              # not moving but PX not yet where it should be: wait this long before believing it
REPORT_EVERY_S = 1.0
HOME_MARGIN_DEG = 10.0      # home search: slack for the poll interval and the switch (user, 2026-10-13)
HOME_LATE_S = 0.2           # home search: time between the last poll and the STOP taking hold
RR_WAIT_S = 2.5             # the manual: no communication for 2 s after RR; half a second of margin
STOP_WAIT_S = 10.0
ABORT_WAIT_S = 3.0

# MST bits (manual, Table 6.5).  MST == 0 is not "idle": bit 3 is the home switch.
MOVING = 0b111
HOME_SWITCH = 1 << 3
LIMIT_INPUTS = (1 << 4) | (1 << 5)
LIMIT_ERRORS = (1 << 6) | (1 << 7)
STATUS_BITS = ((0, "constant speed"), (1, "accelerating"), (2, "decelerating"), (3, "home switch on"),
               (4, "-limit input on"), (5, "+limit input on"), (6, "-limit ERROR (latched)"),
               (7, "+limit ERROR (latched)"), (8, "latch input on"), (9, "Z index on"),
               (10, "TOC watchdog time-out"))

# ---- the commands this script may send (spec, 2026-10-13) ----------------------
READ_ONLY = frozenset("ID VER DN MST PX EO HSPD LSPD ACC MM PS POL IERR RZ HCA RT SL EDIO TOC "
                      "DRVMS DRVRC DRVIC DRVIT R2".split())
ACTIONS = frozenset("ABS INC EO=0 EO=1 STOP ABORT CLR RR".split())
HOMING = frozenset(("HL+", "HL-"))          # "home" mode only
NUMERIC = re.compile(r"(X|PX=)(-?[0-9]{1,9})|(HSPD=|LSPD=|ACC=)([0-9]{1,7})")
ALLOWED = (READ_ONLY, ACTIONS, HOMING, NUMERIC.pattern)
INTEGER = re.compile(r"-?[0-9]{1,10}")
NO_DEVICE_HELP = ("No Performax USB device found. Check the USB cable, that the NSC-A1 has its 24 V "
                  "supply on, and that the Performax USB driver is installed (Device Manager: "
                  "USB\\VID_1589&PID_A101, to be confirmed on the lab PC).")


def _now():
    return time.monotonic()


def _sleep(seconds):
    time.sleep(seconds)


class NscError(RuntimeError):
    """Anything that went wrong talking to the controller."""


class PerformaxError(NscError):
    """The DLL could not be loaded, or a device could not be listed or opened."""


class DeviceError(NscError):
    """No device, or not the one asked for."""


class CommandNotAllowed(NscError):
    """A command outside the allowlist; it was not sent."""


class TransportError(NscError):
    """The USB transaction failed or came back empty (never repeated)."""


class ControllerError(NscError):
    """The controller answered "?..." (it did not do it)."""


class ReplyError(NscError):
    """A reply that does not fit the command: possibly a stale one."""


class OutOfStep(NscError):
    """Replies could not be brought back in step with the commands."""


class NotAnNsc(NscError):
    """The device did not identify as an NSC-A1 (ACE-SDE)."""


class Refused(NscError):
    """A precondition was not met; nothing moved."""


class MotionError(NscError):
    """A move or home search that failed after it was started."""

    def __init__(self, message, stopped, px=None, notes=()):
        super().__init__(message)
        self.stopped, self.px, self.notes = stopped, px, list(notes)


def max_hspd(steps_per_degree=None):
    """The highest HSPD the allowlist lets through: MAX_SPEED at this scale, or
    at the finest micro-stepping while the scale is not known yet."""
    return round(MAX_SPEED * (steps_per_degree or FULL_STEPS_PER_DEG * MAX_DRVMS))


def check_allowed(command, homing=False, steps_per_degree=None):
    """Raise CommandNotAllowed unless the command is on the list above, with
    its number in range.  The ranges are the last line of defence against a
    units mistake in the caller (user, 2026-10-13): a target or a counter
    value far inside the 28-bit range (and within +/-360 deg of the zero
    once the scale is known), MIN_LSPD <= LSPD, HSPD <= MAX_SPEED, 1 <= ACC
    <= MAX_ACC_MS."""
    if command in READ_ONLY or command in ACTIONS or (homing and command in HOMING):
        return
    match = NUMERIC.fullmatch(command)
    if not match:
        raise CommandNotAllowed(f"{command!r} is not on this script's list of allowed commands; it was not sent")
    name, value = (match.group(1), int(match.group(2))) if match.group(1) else (match.group(3), int(match.group(4)))
    top = max_hspd(steps_per_degree)
    if name == "X":
        low, high = (-PX_SAFE, PX_SAFE) if not steps_per_degree else (
            -round(CABLE_LIMIT_DEG * steps_per_degree), round(CABLE_LIMIT_DEG * steps_per_degree))
    elif name == "PX=":
        low, high = -PX_SAFE, PX_SAFE
    elif name == "HSPD=":
        low, high = MIN_LSPD + 1, top
    elif name == "LSPD=":
        low, high = MIN_LSPD, top - 1
    else:
        low, high = 1, MAX_ACC_MS
    if not low <= value <= high:
        raise CommandNotAllowed(f"{command!r}: {value} is outside {low} - {high} for {name.rstrip('=')}; "
                                "it was not sent")


def command_buffer(command):
    """The 64-byte write buffer: the bare upper-case command, NUL-terminated."""
    data = command.encode("ascii")
    if len(data) >= BUFFER_SIZE:
        raise CommandNotAllowed(f"{command!r} is {len(data)} bytes; commands must be shorter than {BUFFER_SIZE}")
    return ctypes.create_string_buffer(data, BUFFER_SIZE)


def clean_reply(raw):
    """Cut at the first NUL, strip CR/LF/spaces and a "#NN" prefix (RT=1)."""
    text = bytes(raw).split(b"\0", 1)[0].decode("ascii", "replace").strip("\r\n ")
    return re.sub(r"^#(?:SDE)?[0-9]{2}", "", text).strip("\r\n ")


def describe_status(mst):
    parts = ["moving" if mst & MOVING else "not moving"]
    parts += [text for bit, text in STATUS_BITS if mst >> bit & 1]
    parts += [f"bit {bit}" for bit in range(11, 32) if mst >> bit & 1]
    return ", ".join(parts)


def angle(px, steps_per_degree):
    if px is None:
        return "an unread position"
    if not steps_per_degree:
        return f"PX {px}"
    return f"{px / steps_per_degree:+.4f} deg"


def describe_error(error):
    if isinstance(error, KeyboardInterrupt):
        return "Ctrl+C"
    return str(error) or type(error).__name__


def looks_like_id(reply):
    """An ID reply ("Ace-Series-SDE"): it contains "SDE" but is not a device name
    like "SDE01", the DN reply, which a stale reply could also bring (user, 2026-10-13)."""
    return "SDE" in reply and not re.fullmatch(r"SDE[0-9]{2}", reply)


# ---- loading the DLL (the tests replace load_performax) -------------------------
def declare_functions(dll):
    """argtypes/restype of every function, from the manual's C prototypes."""
    for name, argtypes in SIGNATURES:
        function = getattr(dll, name, None)     # a WinDLL raises AttributeError for a missing export
        if function is None:
            if name == FLUSH:
                continue
            raise PerformaxError(f"{name} is missing from {DLL_NAME} (an old or a different DLL?)")
        function.argtypes = argtypes
        function.restype = BOOL
    return dll


def dll_folder(dll_dir=None):
    """--dll-dir if given (it must hold the DLL), else this script's folder if it does, else None."""
    if dll_dir:
        folder = os.path.abspath(dll_dir)
        if not os.path.isfile(os.path.join(folder, DLL_NAME)):
            raise PerformaxError(f"{DLL_NAME} is not in --dll-dir {folder}")
        return folder
    if os.path.isfile(os.path.join(SCRIPT_DIR, DLL_NAME)):
        return SCRIPT_DIR
    return None


def python_bits():
    return ctypes.sizeof(ctypes.c_void_p) * 8


def explain_load_error(path, error):
    """The Windows error code, from OSError.winerror or else its exact "[WinError N]"
    text (not any "193" in the message, which also holds the path).

    Python 3.8+ reports a missing module (error 126) as a FileNotFoundError
    "Could not find module ... (or one of its dependencies)", without
    winerror or "[WinError 126]" (user, 2026-10-13)."""
    code = getattr(error, "winerror", None)
    if code is None:
        found = re.search(r"\[WinError (\d+)\]", str(error))
        code = int(found.group(1)) if found else None
    if code == 193:
        return (f"Could not load {path} (WinError 193): it is not a 64-bit DLL. This Python is 64-bit, so "
                f"{DLL_NAME} and {SIUSB_NAME} must both be the 64-bit ones (Arcus \"USB 64-bit DLL v2.06\"), "
                "side by side.")
    if code == 126 or isinstance(error, FileNotFoundError):
        return (f"Could not load {path}: it, or a DLL it needs, was not found (Windows error 126). {DLL_NAME} "
                f"needs {SIUSB_NAME}, and Python does not search PATH for them: put both 64-bit DLLs together "
                "in this script's folder or in --dll-dir.")
    return f"Could not load {path}: {error}"


def load_performax(dll_dir=None):
    """The real loader: Windows only (the tests replace this function).

    Search: --dll-dir, then this script's folder, then the normal Windows DLL
    search.  The folder found is added with os.add_dll_directory, and its
    SiUSBXp.dll is loaded first, so PerformaxCom.dll uses that one."""
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise PerformaxError(f"{DLL_NAME} is a Windows DLL: run this on the lab PC (64-bit Python 3.11 "
                             "on Windows), or try --dry-run")
    if python_bits() != 64:
        # The spec wants 64-bit Python, and the DLLs here are the 64-bit ones; a 32-bit
        # PerformaxCom.dll would also need another calling convention (user, 2026-10-13).
        raise PerformaxError(f"This is a {python_bits()}-bit Python: use 64-bit Python 3.11 "
                             "(py -3.11-64 Newmark_motor_test.py ...), with the 64-bit DLLs")
    folder = dll_folder(dll_dir)
    path = DLL_NAME
    if folder is not None:
        _DLL_DIRECTORIES.append(os.add_dll_directory(folder))
        path = os.path.join(folder, DLL_NAME)
        helper = os.path.join(folder, SIUSB_NAME)
        if os.path.isfile(helper):
            try:
                win_dll(helper)
            except OSError as error:
                raise PerformaxError(explain_load_error(helper, error)) from error
    try:
        dll = win_dll(path)
    except OSError as error:
        raise PerformaxError(explain_load_error(path, error)) from error
    return declare_functions(dll), path


# ---- the controller ------------------------------------------------------------
class HoldCtrlC:
    """While a stop and its report run, Ctrl+C does not raise: a second press
    there could cut them short and lose the "CHECK THE STAGE" message and exit
    status 3.  A press then only asks the stop to go on to ABORT (user,
    2026-10-13).  Nested holds share the outermost one.  Python lets only the
    main thread set the handler; elsewhere (a worker thread) this does
    nothing, and Ctrl+C is not delivered to that thread anyway."""

    def __init__(self, nsc):
        self.nsc = nsc
        self.installed = False
        self.previous = None

    def __enter__(self):
        nsc = self.nsc
        if nsc.hold_depth == 0:
            nsc.abort_asked = False
            try:
                self.previous = signal.signal(signal.SIGINT, nsc.ask_abort)
                self.installed = True
            except (ValueError, OSError, RuntimeError):     # not the main thread
                pass
        nsc.hold_depth += 1
        return self

    def __exit__(self, *_exc):
        self.nsc.hold_depth -= 1
        if self.installed:
            signal.signal(signal.SIGINT, signal.default_int_handler if self.previous is None else self.previous)
        return False


class NSC:
    """The NSC-A1's commands over PerformaxCom.dll, printed as they are sent.

    One thread only: SiUSBXp.dll keeps its handles per thread, so every DLL
    call of a session must come from the thread that opened it (here the
    main thread)."""

    def __init__(self, dll, echo=print, allow_homing=False):
        self.dll = dll
        self.echo = echo
        self.allow_homing = allow_homing
        self.handle = None
        self.identified = False
        self.need_resync = False
        self.motion_may_run = False     # a move command was sent and it has not been seen stopped since
        self.motion_sent = False        # this run sent a move command (X, HL)
        self.stop_finished = False      # a stop sequence ran to its end (stopped or not)
        self.steps_per_degree = None
        self.hold_depth = 0             # HoldCtrlC nesting
        self.abort_asked = False        # Ctrl+C pressed during a hold: go on to ABORT
        self.home_watch = None          # home mode: the search's HomeWatch, so every report can say what PX counts from
        self.closing_code = None        # close_and_report's exit status, kept in case it has to run again
        self._no_flush_said = False

    def hold_ctrl_c(self):
        return HoldCtrlC(self)

    def ask_abort(self, _signum, _frame):
        """The SIGINT handler during a hold: only a flag (printing here could
        collide with a print it interrupted)."""
        self.abort_asked = True

    # ---- devices -------------------------------------------------------------
    def _product_string(self, index, option):
        buffer = ctypes.create_string_buffer(PRODUCT_STRING_SIZE)
        if not self.dll.fnPerformaxComGetProductString(index, buffer, option):
            return None
        return buffer.value.decode("ascii", "replace").strip()

    def devices(self):
        """[(index, serial, description)] from the DLL's own enumeration."""
        count = DWORD(0)
        if not self.dll.fnPerformaxComGetNumDevices(ctypes.pointer(count)):
            raise PerformaxError("fnPerformaxComGetNumDevices failed (is the Performax USB driver installed?)")
        return [(index, self._product_string(index, SERIAL_OPTION), self._product_string(index, DESCRIPTION_OPTION))
                for index in range(count.value)]

    def choose_device(self, serial=None):
        found = self.devices()
        self.echo(f"USB: {len(found)} Performax device(s)")
        for index, number, text in found:
            self.echo(f"  device {index}: serial {number!r}, {text!r}")
        if not found:
            raise DeviceError(NO_DEVICE_HELP)
        serials = ", ".join(repr(number) for _index, number, _text in found)
        if serial:
            matches = [index for index, number, _text in found if number == serial.strip()]
            if len(matches) != 1:
                raise DeviceError(f"{len(matches)} devices have the serial {serial.strip()!r} (found: {serials})")
            self.echo(f"Using device {matches[0]} (serial {serial.strip()!r}).")
            return matches[0]
        if len(found) > 1:
            raise DeviceError(f"{len(found)} Performax devices are plugged in: say which is the NSC-A1 with "
                              f"--serial (one of {serials})")
        self.echo("Using device 0 (the only one).")
        return 0

    def open(self, index):
        self.echo(f"> (timeouts {TIMEOUT_MS} ms)")
        if not self.dll.fnPerformaxComSetTimeouts(TIMEOUT_MS, TIMEOUT_MS):
            raise PerformaxError("fnPerformaxComSetTimeouts failed")
        handle = HANDLE()
        self.echo(f"> (open device {index})")
        if not self.dll.fnPerformaxComOpen(index, ctypes.pointer(handle)) or not handle.value:
            raise PerformaxError(f"Could not open device {index} (is QuickMotion or another program using it?)")
        self.handle = handle
        self._flush()

    def _flush(self):
        """fnPerformaxComFlush if this DLL has it: right after Open and after a failed transaction."""
        flush = getattr(self.dll, FLUSH, None)
        if flush is None:
            if not self._no_flush_said:
                self.echo(f"  (this {DLL_NAME} has no {FLUSH}: an older DLL)")
                self._no_flush_said = True
            return False
        self.echo("> (flush)")
        try:
            ok = bool(flush(self.handle))
        except Exception as error:
            self.echo(f"  (flush failed: {error})")
            return False
        if not ok:
            self.echo("  (flush returned FALSE)")
        return ok

    def close(self):
        """STOP first if a move may still be running (bounded), then close the
        handle.  Returns (stopped, PX, notes) when it had to stop, else None."""
        if self.handle is None:
            return None
        outcome = None
        try:
            with self.hold_ctrl_c():
                if self.motion_may_run and not self.stop_finished:
                    self.echo("Closing while a move may be running: STOP first.")
                    outcome = self.stop_motion()
        except BaseException as error:
            outcome = (False, None, [f"the stop on closing failed: {describe_error(error)}"])
        finally:
            try:
                self.echo("> (close)")
                self.dll.fnPerformaxComClose(self.handle)
            except BaseException:
                pass
            self.handle = None
        return outcome

    # ---- one command ---------------------------------------------------------
    def _transact(self, command, quiet=False):
        """Send one allowed command and return its reply (never repeated)."""
        check_allowed(command, self.allow_homing, self.steps_per_degree)
        write = command_buffer(command)
        if self.handle is None:
            raise NscError("the controller is not open")
        read = ctypes.create_string_buffer(BUFFER_SIZE)
        if not quiet:
            self.echo(f"> {command}")
        finished = False
        try:
            ok = self.dll.fnPerformaxComSendRecv(self.handle, write, BUFFER_SIZE, BUFFER_SIZE, read)
            finished = True
        finally:
            if not finished:            # Ctrl+C or an error mid-transaction: its reply may come later
                self.need_resync = True
        if not ok:
            self.need_resync = True
            if quiet:
                self.echo(f"> {command}")
            self.echo("< (no reply: the USB transaction failed)")
            self._flush()
            raise TransportError(f"{command}: the USB transaction failed (it is not repeated)")
        reply = clean_reply(read.raw)
        if not quiet:
            self.echo(f"< {reply}")
        if not reply:
            self.need_resync = True
            raise TransportError(f"{command}: an empty reply")
        if reply.startswith("?"):
            if quiet:
                self.echo(f"> {command}\n< {reply}")
            raise ControllerError(f"{command}: the controller answered {reply!r}")
        return reply

    def resync(self):
        """After a failure: flush, then ID and VER.  ID with "SDE", then a VER
        reply that is not the ID reply, show that they are back in step (one
        behind, VER would get the ID reply)."""
        self.echo("  (replies may be out of step: flush, then ID and VER)")
        self._flush()
        identity = self._transact("ID")
        if not looks_like_id(identity):
            raise OutOfStep(f"after a flush ID answered {identity!r}: replies are out of step")
        version = self._transact("VER")
        if version == identity:
            raise OutOfStep(f"VER answered {version!r}, the ID reply: replies are still one behind")
        self.need_resync = False
        self.echo("  (replies back in step)")

    def query(self, command, quiet=False):
        if self.need_resync:
            self.resync()
        return self._transact(command, quiet)

    def number(self, command, quiet=False):
        reply = self.query(command, quiet)
        if not INTEGER.fullmatch(reply):
            self.need_resync = True
            raise ReplyError(f"{command} answered {reply!r}, not a number (a stale reply?)")
        return int(reply)

    def set_ok(self, command):
        reply = self.query(command)
        if reply != "OK":
            self.need_resync = True
            raise ReplyError(f"{command} answered {reply!r}, not OK (a stale reply?)")

    def status(self, quiet=False):
        mst = self.number("MST", quiet)
        if mst < 0:
            self.need_resync = True
            raise ReplyError(f"MST answered {mst} (a stale reply?)")
        return mst

    # ---- identity ------------------------------------------------------------
    def identify(self):
        """ID before anything else; then VER, whose different reply shows they are in step."""
        try:
            identity = self._transact("ID")
        except ControllerError as error:
            raise NotAnNsc(f"{error}: not an NSC-A1") from error
        if not looks_like_id(identity):
            raise NotAnNsc(f"The device answered ID with {identity!r}, not an NSC-A1 (\"Ace-Series-SDE\"). "
                           "If it is the NSC-A1, the reply may be a stale one: unplug and replug the USB cable "
                           "and try again.")
        version = self._transact("VER")
        if version == identity:
            self.need_resync = True
            raise OutOfStep(f"VER answered {version!r}, the ID reply: replies are out of step; unplug and "
                            "replug the USB cable and try again")
        self.identified = True
        return identity, version

    # ---- checks and settings -------------------------------------------------
    def check_ready(self):
        """Before every move: not moving, no latched limit error, no limit input, EO = 1."""
        mst = self.status()
        if mst & MOVING:
            raise Refused(f"it is moving (MST {mst}: {describe_status(mst)}); stop it first")
        if mst & LIMIT_ERRORS:
            raise Refused(f"a limit error is latched (MST {mst}: {describe_status(mst)}); clear it on purpose "
                          "with 'clear' first")
        if mst & LIMIT_INPUTS:
            raise Refused(f"a limit input is on at rest (MST {mst}: {describe_status(mst)}), but the RM-5-110 "
                          "has no limit switches: a polarity problem; fix the limit polarity in Newmark's "
                          "QuickMotion, not here")
        eo = self.number("EO")
        if eo != 1:
            raise Refused(f"the motor driver is off (EO={eo}): the position counter would advance without "
                          "motion; switch it on with 'enable'")
        return mst

    def read_driver(self):
        """DRVMS (and DRVRC/DRVIC/DRVIT for display) via RR; nothing is written."""
        mst = self.status()
        if mst & MOVING:
            raise Refused("it is moving, and RR cuts communication for 2 s")
        sl, edio = self.number("SL"), self.number("EDIO")
        if sl or edio:
            raise Refused(f"StepNLoop (SL={sl}) or DIO control (EDIO={edio}) is on; the manual wants both off "
                          "while the driver is read: switch them off in QuickMotion")
        self.set_ok("RR")
        self.echo(f"  (waiting {RR_WAIT_S:g} s: no communication while the driver is read)")
        _sleep(RR_WAIT_S)
        r2 = self.number("R2")
        if r2 != 1:
            raise Refused(f"the driver read failed (R2 = {r2}; 1 means success)")
        microsteps = self.number("DRVMS")
        if not 2 <= microsteps <= 500:
            raise Refused(f"DRVMS = {microsteps}, outside 2 - 500")
        others = {}
        for name in ("DRVRC", "DRVIC", "DRVIT"):
            try:
                others[name] = self.number(name)
            except NscError as error:
                self.echo(f"  ({name} could not be read: {error})")
                others[name] = None
        self.steps_per_degree = FULL_STEPS_PER_DEG * microsteps
        return microsteps, others

    def set_speeds(self, hspd, lspd, acc):
        """Runtime registers (not stored): set, then read back."""
        if not MIN_LSPD <= lspd < hspd:
            raise CommandNotAllowed(f"LSPD {lspd} must be at least {MIN_LSPD} and below HSPD {hspd}; nothing was sent")
        self.set_ok(f"HSPD={hspd}")
        self.set_ok(f"LSPD={lspd}")
        self.set_ok(f"ACC={acc}")
        hspd_read, lspd_read, acc_read = self.number("HSPD"), self.number("LSPD"), self.number("ACC")
        if (hspd_read, lspd_read) != (hspd, lspd):
            raise Refused(f"the speeds read back HSPD {hspd_read}, LSPD {lspd_read} (set {hspd}, {lspd})")
        if acc_read <= 0:
            raise Refused(f"ACC read back {acc_read} ms")
        if acc_read != acc:
            self.echo(f"  (the controller adjusted ACC to {acc_read} ms)")
        return acc_read

    # ---- motion ----------------------------------------------------------------
    def start_motion(self, command):
        """X<target> or HL+/HL-.  Flagged before sending: a failed reply does not
        prove the move did not start, so it is never repeated, only stopped."""
        self.motion_may_run = True
        self.motion_sent = True
        self.stop_finished = False
        self.set_ok(command)

    def wait_motion(self, timeout_s, what="the move", expect_px=None, watch=None):
        """Poll MST and PX every 50 ms until it has ended; past the time-out, when
        ``watch(px, now)`` gives a reason, or on a communication failure, STOP and
        raise.  Returns (seconds, PX, MST).

        Ended means IDLE_READS polls in a row not moving, and PX at expect_px
        (or still elsewhere after SETTLE_S not moving).  One idle read is not
        enough: the controller may set its moving bits a moment after its OK,
        and HL may pause between its stages (user, 2026-10-13)."""
        spd = self.steps_per_degree
        started = _now()
        next_line, last, reason = started, None, None
        idle, idle_since = 0, None
        try:
            _sleep(POLL_S)
            while True:
                mst = self.status(quiet=True)
                px = self.number("PX", quiet=True)
                now = _now()
                if watch is not None:
                    reason = watch(px, now)
                    if reason is not None:
                        break
                if mst & MOVING:
                    idle, idle_since = 0, None
                else:
                    idle += 1
                    idle_since = now if idle_since is None else idle_since
                    if idle >= IDLE_READS and (expect_px is None or px == expect_px
                                               or now - idle_since >= SETTLE_S):
                        break
                if now - started > timeout_s:
                    reason = f"{what} did not end within {timeout_s:.0f} s"
                    break
                if mst != last or now >= next_line:
                    self.echo(f"  {now - started:6.1f} s  MST {mst} ({describe_status(mst)})  "
                              f"PX {px} ({angle(px, spd)})")
                    last, next_line = mst, now + REPORT_EVERY_S
                _sleep(POLL_S)
        except NscError as error:
            reason = f"communication failed during {what}: {error}"
        if reason is not None:
            self.echo(f"{reason}: STOP")
            stopped, px, notes = self.stop_motion()
            raise MotionError(reason, stopped, px, notes)
        self.motion_may_run = False
        elapsed = _now() - started
        self.echo(f"  {elapsed:6.1f} s  MST {mst} ({describe_status(mst)})")
        return elapsed, self.number("PX"), mst

    def _send_stop(self, command, notes):
        try:
            reply = self._transact(command)     # sent even when replies are out of step
            if reply != "OK":
                self.need_resync = True
                notes.append(f"{command} answered {reply!r}")
        except BaseException as error:          # Ctrl+C included: the stop goes on
            notes.append(f"{command}: {describe_error(error)}")

    def _wait_until_still(self, wait_s, notes, escalate=False):
        """(True, PX) once IDLE_READS status reads in a row say it is not moving,
        with PX the same at each; (False, None) after wait_s or a Ctrl+C (with
        ``escalate``, also a Ctrl+C held by HoldCtrlC).

        One read is not enough here either (see wait_motion): a STOP that lands
        just after a motion command's OK, or between HL stages, may do nothing,
        and the motion would then go on unwatched (user, 2026-10-13)."""
        deadline = _now() + wait_s
        idle, still_px = 0, None
        while True:
            try:
                mst = self.status(quiet=True)
                if mst & MOVING:
                    idle = 0
                else:
                    px = self.number("PX", quiet=True)
                    idle = idle + 1 if idle and px == still_px else 1
                    still_px = px
                    if idle >= IDLE_READS:
                        self.echo(f"  MST {mst} ({describe_status(mst)})  PX {px}")
                        break
            except KeyboardInterrupt:
                notes.append("Ctrl+C while waiting for the stop")
                return False, None
            except Exception as error:
                idle = 0
                text = describe_error(error)
                if text not in notes:
                    notes.append(text)
            if escalate and self.abort_asked:
                notes.append("Ctrl+C while waiting for the stop")
                return False, None
            if _now() >= deadline:
                return False, None
            try:
                _sleep(POLL_S)
            except KeyboardInterrupt:
                notes.append("Ctrl+C while waiting for the stop")
                return False, None
        self.motion_may_run = False
        return True, still_px

    def stop_motion(self, wait_s=STOP_WAIT_S):
        """STOP (decelerated); wait until it is not moving; ABORT if STOP did not
        stop it.  Returns (stopped, PX, notes); every step is tried even after a
        Ctrl+C, as stopping is the safe state."""
        self.stop_finished = False
        notes = []
        with self.hold_ctrl_c():
            self._send_stop("STOP", notes)
            stopped, px = self._wait_until_still(wait_s, notes, escalate=True)
            if not stopped:
                self.echo("Not stopped after STOP: ABORT")
                self._send_stop("ABORT", notes)
                stopped, px = self._wait_until_still(ABORT_WAIT_S, notes)
            self.stop_finished = True   # close() repeats it only if this was cut short
        return stopped, px, notes


# ---- --dry-run: a simulated controller --------------------------------------------
class SimulatedNSC:
    """An NSC-A1 that answers as its manual describes, with a stage that turns in
    (simulated) time: DRVMS 250, so 10000 steps/deg; the home switch is 37.5 deg
    on (+) from where the stage starts (PX ``px``).

    A motion is one segment {from, to, t0, t1} at a time, and HL queues the
    rest of its stages (manual, Figure 6.6): at the switch the counter becomes
    0, it runs on while it decelerates, comes back to the switch at low speed,
    goes past it by HCA and comes back to it at low speed (counter 0)."""

    def __init__(self, serials=("SIM-NSC-A1",), identity="Ace-Series-SDE", px=0):
        self.serials = list(serials)
        self.identity = identity
        self.version, self.name = "V231BL", "SDE01"
        self.handle = None
        self.calls = []             # DLL-level calls: ("timeouts", r, w), ("open", i), ("flush",), ("close", h)
        self.received = []          # every command, in order
        self.px, self.eo, self.mm = px, 1, 0
        self.hspd, self.lspd, self.acc = 1000, 100, 300
        self.inputs = 0             # MST input bits (3 home, 4/5 limit inputs, 8-10)
        self.latched = 0            # MST bits 6/7
        self.registers = {"POL": 0, "IERR": 0, "RZ": 0, "HCA": 0, "RT": 0, "SL": 0, "EDIO": 0, "TOC": 0}
        self.driver = {"DRVMS": 250, "DRVRC": 2000, "DRVIC": 500, "DRVIT": 50}
        self.driver_read = False
        self.silent_until = -math.inf   # RR: no communication for 2 s
        self.home_px = 375000 + px      # the home switch, in counter steps (+37.5 deg from where it started)
        self.motion = None

    # ---- the DLL's view ----------------------------------------------------
    def count(self):
        return len(self.serials)

    def product_string(self, index, option):
        if not 0 <= index < len(self.serials) or option not in (SERIAL_OPTION, DESCRIPTION_OPTION):
            return None
        return self.serials[index] if option == SERIAL_OPTION else "NSC-A1 (simulated)"

    def set_timeouts(self, read_ms, write_ms):
        self.calls.append(("timeouts", read_ms, write_ms))
        return True

    def open(self, index):
        if not 0 <= index < len(self.serials):
            return None
        self.calls.append(("open", index))
        self.handle = 0x5EED0 + index
        return self.handle

    def close(self, handle):
        self.calls.append(("close", handle))
        ok = handle == self.handle
        self.handle = None
        return ok

    def flush(self, handle):
        self.calls.append(("flush",))
        return handle == self.handle

    def send_recv(self, handle, data):
        """The 64-byte write buffer in, the reply bytes out; None = the transaction failed."""
        if self.handle is None or handle != self.handle or _now() < self.silent_until:
            return None
        command = bytes(data).split(b"\0", 1)[0].decode("ascii", "replace")
        self.received.append(command)
        text = self.reply(command)
        if self.registers["RT"] == 1:
            text = "#01" + text
        return (text + "\r").encode("ascii")

    # ---- the controller ------------------------------------------------------
    def turn(self):
        return 360 * FULL_STEPS_PER_DEG * self.driver["DRVMS"]

    def reply(self, command):
        now = _now()
        self._advance(now)
        moving = self.motion is not None
        reads = {"ID": self.identity, "VER": self.version, "DN": self.name, "MST": self._status(now),
                 "PX": self._position(now), "EO": self.eo, "HSPD": self.hspd, "LSPD": self.lspd,
                 "ACC": self.acc, "MM": self.mm, "PS": self.hspd if moving else 0,
                 "R2": 1 if self.driver_read else 0}
        reads.update(self.registers)
        # Driver values are valid only after RR (manual, Table 6.11).
        reads.update({name: value if self.driver_read else 0 for name, value in self.driver.items()})
        if command in reads:
            return str(reads[command])
        if command in ("ABS", "INC"):
            self.mm = 0 if command == "ABS" else 1
            return "OK"
        if command in ("EO=0", "EO=1"):
            self.eo = int(command[-1])
            return "OK"
        if command == "CLR":
            self.latched = 0
            return "OK"
        if command in ("STOP", "ABORT"):
            self._stop(command, now)
            return "OK"
        if command == "RR":
            self.driver_read = True
            self.silent_until = now + 2.0
            return "OK"
        name, equals, value = command.partition("=")
        if equals and name in ("HSPD", "LSPD", "ACC") and value.isdigit():
            setattr(self, name.lower(), int(value))
            return "OK"
        if equals and name == "PX" and INTEGER.fullmatch(value):
            if moving:
                return "?Moving"
            self.home_px += int(value) - self.px
            self.px = int(value)
            return "OK"
        if re.fullmatch(r"X-?[0-9]+", command) or command in ("HL+", "HL-"):
            if moving:
                return "?Moving"
            if self.latched:
                return "?State Error"
            if command.startswith("X"):
                target = int(command[1:]) if self.mm == 0 else self.px + int(command[1:])
                self._start(target, now)
            else:
                self._start_home(1 if command == "HL+" else -1, now)
            return "OK"
        return "?" + command

    def _start(self, target, now, homing=False, queue=()):
        duration = abs(target - self.px) / max(self.hspd, 1) + self.acc / 1000
        self.motion = {"from": self.px, "to": target, "t0": now, "t1": now + duration,
                       "homing": homing, "stopping": False, "queue": list(queue)}

    def _start_home(self, sign, now):
        """HL+/HL-: to the switch (at most one turn), then the stages after it."""
        turn = self.turn()
        switch = self.px + sign * ((sign * (self.home_px - self.px)) % turn or turn)
        overshoot = round((self.hspd + self.lspd) / 2 * self.acc / 1000)
        hca = abs(self.registers["HCA"])
        stages = [{"reset": True, "to": sign * overshoot, "duration": self.acc / 1000, "mst": 4},
                  {"to": 0, "duration": overshoot / max(self.lspd, 1), "mst": 1}]
        if hca:
            stages += [{"to": -sign * hca, "duration": hca / max(self.hspd, 1) + self.acc / 1000},
                       {"to": 0, "duration": hca / max(self.lspd, 1), "mst": 1}]
        self._start(switch, now, homing=True, queue=stages)

    def _position(self, now):
        motion = self.motion
        if motion is None:
            return self.px
        span = motion["t1"] - motion["t0"]
        fraction = 1.0 if span <= 0 else min(1.0, max(0.0, (now - motion["t0"]) / span))
        return round(motion["from"] + (motion["to"] - motion["from"]) * fraction)

    def _finished(self, now):
        return now >= self.motion["t1"]

    def _end_position(self, motion):
        return motion["to"]

    def _advance(self, now):
        while self.motion is not None and self._finished(now):
            motion, self.motion = self.motion, None
            self.px = self._end_position(motion)
            queue = motion.get("queue") or []
            if motion["stopping"] or not queue:
                return
            stage, t0 = queue[0], motion["t1"]
            if stage.get("reset"):
                self.px, self.home_px = 0, 0    # HL: the counter becomes 0 at the switch
            self.motion = {"from": self.px, "to": stage["to"], "t0": t0, "t1": t0 + stage["duration"],
                           "homing": True, "stopping": False, "idle": stage.get("idle", False),
                           "mst": stage.get("mst"), "queue": queue[1:]}

    def _status(self, now):
        bits = self.inputs | self.latched
        motion = self.motion
        if motion is not None and not motion.get("idle"):
            ramp = self.acc / 1000
            if motion["stopping"]:
                bits |= 4               # STOP decelerates (manual: "STOP will decelerate")
            elif motion.get("mst"):
                bits |= motion["mst"]   # an HL stage at one speed (low speed, or slowing down)
            else:
                bits |= 2 if now - motion["t0"] < ramp else 4 if motion["t1"] - now < ramp else 1
        elif motion is None and (self.px - self.home_px) % self.turn() == 0:
            bits |= HOME_SWITCH
        return bits

    def _stop(self, command, now):
        motion = self.motion
        if motion is None:
            return
        if command == "ABORT":
            self.px, self.motion = self._position(now), None
            return
        # STOP: from the present speed down to LSPD within ACC, then stop (manual, "Stopping Motor").
        ramp = min(max(motion["t1"] - now, 0.0), self.acc / 1000)
        span = motion["t1"] - motion["t0"]
        speed = 0.0 if span <= 0 or motion.get("idle") else abs(motion["to"] - motion["from"]) / span
        here = self._position(now)
        way = 1 if motion["to"] >= motion["from"] else -1
        there = here + way * min(round((speed + min(self.lspd, speed)) / 2 * ramp), abs(motion["to"] - here))
        self.motion = dict(motion, **{"from": here, "to": there, "t0": now, "t1": now + ramp, "stopping": True,
                                      "idle": False, "queue": []})


def _handle_value(handle):
    return getattr(handle, "value", handle)


class SimulatedPerformax:
    """PerformaxCom.dll's functions over a SimulatedNSC (for --dry-run), called
    exactly as the real DLL is called above."""

    def __init__(self, controller=None):
        self.controller = SimulatedNSC() if controller is None else controller

    def fnPerformaxComGetNumDevices(self, count):
        count[0] = self.controller.count()
        return 1

    def fnPerformaxComGetProductString(self, index, buffer, option):
        text = self.controller.product_string(int(index), int(option))
        if text is None:
            return 0
        data = text.encode("ascii")[:PRODUCT_STRING_SIZE - 1] + b"\0"
        ctypes.memmove(buffer, data, len(data))
        return 1

    def fnPerformaxComOpen(self, index, handle):
        value = self.controller.open(int(index))
        if value is None:
            return 0
        handle[0] = value
        return 1

    def fnPerformaxComClose(self, handle):
        return int(self.controller.close(_handle_value(handle)))

    def fnPerformaxComSetTimeouts(self, read_ms, write_ms):
        return int(self.controller.set_timeouts(int(read_ms), int(write_ms)))

    def fnPerformaxComSendRecv(self, handle, write, write_size, read_size, read):
        reply = self.controller.send_recv(_handle_value(handle), ctypes.string_at(write, int(write_size)))
        if reply is None:
            return 0
        ctypes.memmove(read, reply[:int(read_size)].ljust(int(read_size), b"\0"), int(read_size))
        return 1

    def fnPerformaxComFlush(self, handle):
        return int(self.controller.flush(_handle_value(handle)))


# ---- the modes ---------------------------------------------------------------
def confirm(assume_yes, action):
    if assume_yes:
        print(f"(--yes: {action} without asking)")
        return True
    try:
        answer = input(f'Type "yes" to {action}: ')
    except EOFError:
        return False
    return answer.strip().lower() == "yes"


def scale_text(microsteps, steps_per_degree):
    return (f"{steps_per_degree} steps/deg ({GEAR_RATIO}:1 x {FULL_STEPS_PER_TURN} full steps x DRVMS "
            f"{microsteps} / 360)")


def speed_registers(speed, steps_per_degree):
    """HSPD for the speed, LSPD = HSPD / 10 (at least 10 pps), ACC 300 ms."""
    hspd = round(speed * steps_per_degree)
    lspd = max(10, hspd // 10)
    if hspd <= lspd:
        raise Refused(f"{speed:g} deg/s is too slow at {steps_per_degree} steps/deg (HSPD {hspd} pps)")
    return hspd, lspd, ACC_MS


class HomePlan:
    """The numbers of one home search HL+/HL- from PX ``start``, in steps and
    seconds (user, 2026-10-13).

    The switch is at most one turn ahead.  After it the counter is 0 there,
    and the stage runs on by ``overshoot`` while it decelerates from HSPD to
    LSPD, comes back, goes past by HCA and comes back (manual, Figure 6.6).
    HomeWatch stops it (STOP, which decelerates by ``overshoot`` again) at
    one turn + ``margin`` before the switch, or ``overshoot`` + |HCA| +
    ``margin`` from it after.  So it stays between ``back`` and ``far``."""

    def __init__(self, start, sign, steps_per_degree, hspd, lspd, acc_ms, hca):
        self.start, self.sign, self.spd = start, sign, steps_per_degree
        self.hspd, self.lspd, self.acc_ms = hspd, lspd, acc_ms
        self.turn = round(360 * steps_per_degree)
        self.hca = abs(hca)
        self.margin = round(HOME_MARGIN_DEG * steps_per_degree)
        # Slowing down over ACC is the default (EDEC = 0).  With EDEC = 1, stored
        # in flash, HL and STOP slow down over DEC, which is not on this script's
        # list of reads: check EDEC = 0 in QuickMotion (user, 2026-10-13).
        self.overshoot = round((hspd + lspd) / 2 * acc_ms / 1000)
        late = round(hspd * HOME_LATE_S)
        self.ahead = self.turn + self.hca + 2 * self.overshoot + self.margin + late
        self.behind = self.hca + 2 * self.overshoot + self.margin + late
        self.far = start + sign * self.ahead
        self.back = start - sign * self.behind
        self.expected = ((self.turn + self.hca) / hspd + (self.overshoot + self.hca) / lspd + 4 * acc_ms / 1000)
        self.timeout = 1.2 * self.expected + 2      # a back-up: the watch bounds it by travel first

    def shift_needed(self):
        """The turn, in whole degrees with its sign, after which every point the
        search may reach is within +/-CABLE_LIMIT_DEG of the zero: 0 if it is
        already, None if no start is."""
        limit = round(CABLE_LIMIT_DEG * self.spd)
        over_far = self.sign * self.far - limit         # steps beyond the limit, ahead
        over_back = -self.sign * self.back - limit      # steps beyond the limit, behind
        if over_far <= 0 and over_back <= 0:
            return 0
        if self.ahead + self.behind > 2 * limit:
            return None
        return -self.sign * math.ceil(over_far / self.spd) if over_far > 0 else \
            self.sign * math.ceil(over_back / self.spd)

    def check_wires(self):
        """Refuse unless every point it may reach is within +/-CABLE_LIMIT_DEG of the zero."""
        shift = self.shift_needed()
        if shift == 0:
            return
        if shift is None:
            advice = "no start keeps it within the limit (is HCA too large?)"
        elif abs(shift) <= MAX_MOVE_DEG:
            advice = f"first move it {abs(shift)} deg (move --degrees {shift:+d}), then search again"
        else:
            # One 'move' turns at most MAX_MOVE_DEG, so advice beyond it would be a
            # usage error; from within the limit the other direction then always
            # fits after a short move (user, 2026-10-13).
            way = "-" if self.sign > 0 else "+"
            other = HomePlan(self.start, -self.sign, self.spd, self.hspd, self.lspd, self.acc_ms,
                             self.hca).shift_needed()
            if other == 0:
                advice = f"search the other way instead (home --direction {way})"
            elif other is not None and abs(other) <= MAX_MOVE_DEG:
                advice = (f"one move turns at most {MAX_MOVE_DEG:g} deg, so search the other way instead: "
                          f"move --degrees {other:+d}, then home --direction {way}")
            else:
                advice = ("moves within the limit cannot bring it back from there: run 'zero' first where the "
                          "sample wires hang free")
        raise Refused(f"a home search HL{'+' if self.sign > 0 else '-'} from {angle(self.start, self.spd)} may "
                      f"reach {angle(self.back, self.spd)} to {angle(self.far, self.spd)} from the zero (one turn "
                      f"to the switch, HCA = {self.hca} steps, the slowing down and a {HOME_MARGIN_DEG:g} deg "
                      f"margin), beyond the +/-{CABLE_LIMIT_DEG:g} deg limit for the sample wires; {advice}")


class HomeWatch:
    """Bounds a home search by travel, not only by time (user, 2026-10-13).

    The RM-5-110 turns without end and has no limit switches, so a home switch
    that never triggers (unwired, wrong polarity, broken) would let HL turn on
    and wind up the sample wires.  PX is read at every poll.  Before the
    switch it counts on from the start: more than one turn + margin means no
    switch, so STOP.  At the switch the controller sets the counter to 0,
    seen as a step PX could not make by turning (backwards, or faster than
    HSPD); from then on PX counts from the switch, and the rest of HL stays
    within overshoot + |HCA| + margin of it.

    A switch within about one poll of the old zero makes its reset look like
    a normal step.  The first backward step is then HL's turn back after
    slowing down (manual, Figure 6.6), and the poll before it already counts
    from the switch.  Such a step is ambiguous: either way the switch is
    within near_deg() of the old zero, so only that range is reported
    (``near_zero``).  The bounds hold either way: PX counts from the switch
    by then (user, 2026-10-13)."""

    def __init__(self, plan, start, now):
        self.plan, self.start = plan, start
        self.last_px, self.last_time = start, now
        self.switch = None      # PX (old count) at the poll before the counter reset: about where the switch is
        self.near_zero = False  # the switch is within near_deg() of the old zero; ``switch`` is not more exact
        self.most = plan.hspd * (1.5 * POLL_S + 0.1)    # the largest step allowed in one poll so far

    def near_deg(self):
        """How close to the old zero a switch may be for its reset to pass for a
        normal step: the slowing down and two polls of travel, in degrees."""
        return (self.plan.overshoot + 2 * self.most) / self.plan.spd

    def __call__(self, px, now):
        """None while the search is within its bounds, else why it must be stopped."""
        plan, spd = self.plan, self.plan.spd
        if self.switch is None:
            step = plan.sign * (px - self.last_px)
            most = plan.hspd * (1.5 * max(now - self.last_time, 0.0) + 0.1)
            self.most = max(self.most, most)
            if step < 0 or step > most:
                self.switch = self.last_px
                # A backward step from within the slowing down + one poll of 0 may be
                # the turn back after a reset that passed for a normal step.
                self.near_zero = step < 0 and 0 <= plan.sign * self.last_px <= plan.overshoot + most
            elif plan.sign * (px - self.start) > plan.turn + plan.margin:
                return (f"no home switch within one turn: the home search turned {angle(abs(px - self.start), spd)} "
                        "without the counter reset at the switch (is the home switch wired to the DB9 Signals "
                        "connector, and is its polarity right in QuickMotion?)")
        self.last_px, self.last_time = px, now
        if self.switch is not None and abs(px) > plan.overshoot + plan.hca + plan.margin:
            return (f"after the counter reset at the switch the home search went on to {angle(px, spd)} from it "
                    f"(more than the slowing down + HCA + {HOME_MARGIN_DEG:g} deg); positions now count from the "
                    "switch")
        return None


def move_text(shift):
    """The 'move' command(s) that turn ``shift`` deg (one turns at most
    MAX_MOVE_DEG), or None when it is less than 0.1 deg."""
    shift = round(shift, 1)
    if abs(shift) < 0.1:
        return None
    if abs(shift) <= MAX_MOVE_DEG:
        return f"move --degrees {shift:+.1f}"
    first = math.copysign(MAX_MOVE_DEG, shift)
    return f"move --degrees {first:+.0f}, then move --degrees {shift - first:+.1f}"


def old_zero_text(watch, px):
    """After the counter reset at the switch: where the old zero (where the
    sample wires should hang free) now is, and the way back there from PX
    ``px`` (None: not known)."""
    if watch.near_zero:
        near = watch.near_deg()
        return (f"The home switch is within about {near:.1f} deg of the old zero (where the sample wires should "
                f"hang free), so the old zero is within about {near:.1f} deg of 0 deg now: turn to where the "
                "wires hang free and run 'zero' there.")
    spd = watch.plan.spd
    old = -watch.switch / spd
    text = (f"The home switch is about {angle(watch.switch, spd)} from the old zero (where the sample wires "
            f"should hang free), so the old zero is now about {old:+.1f} deg")
    if px is None:
        return text + ": read the position ('info'), turn back there with 'move' and run 'zero' there."
    way = move_text(old - px / spd)
    if way is None:
        return text + ": it is about here; run 'zero' if the wires hang free here."
    return text + f": to turn back there, {way}, then run 'zero'."


def home_note(nsc, px):
    """After a home search that did not end as planned, what its angles count
    from: a report against the switch alone would hide that the zero and the
    +/-360 deg limit of 'move' moved there (user, 2026-10-13).  None outside a
    home search."""
    watch = nsc.home_watch
    if watch is None or not nsc.motion_sent:
        return None
    if watch.switch is None:
        return (f"No counter reset at the home switch was seen: angles still count from the old zero, or, if the "
                f"switch was passed within about {watch.near_deg():.1f} deg of it (a reset there looks like a "
                "normal step), from the switch, about the same place.")
    return ("The counter was reset at the home switch: angles, and the +/-360 deg wire limit of 'move', now count "
            "from it. " + old_zero_text(watch, px))


def mode_info(nsc, args):
    """Read only (and RR, which reads the driver's parameters)."""
    values, failed = {}, False
    for command in ("DN", "MST", "EO", "PX", "MM", "HSPD", "LSPD", "ACC", "PS", "POL", "IERR", "RZ", "HCA",
                    "RT", "SL", "EDIO", "TOC"):
        try:
            values[command] = nsc.query(command) if command == "DN" else nsc.number(command)
        except NscError as error:
            print(f"  ({command} could not be read: {error})")
            values[command], failed = None, True
    driver, why_not = None, None
    try:
        driver = nsc.read_driver()
    except Refused as error:
        why_not = str(error)
    except NscError as error:
        why_not, failed = str(error), True
    spd = nsc.steps_per_degree
    get = values.get

    print("\nSummary")
    print(f"  device name (DN): {get('DN')}")
    mst = get("MST")
    print(f"  status: MST {mst}" + ("" if mst is None else f" = {describe_status(mst)}"))
    print(f"  motor driver: EO {get('EO')}" + {1: " (on)", 0: " (off)"}.get(get("EO"), ""))
    px = get("PX")
    print(f"  position: PX {px}" + ("" if px is None or not spd else f" = {angle(px, spd)} from the zero"))
    hspd = get("HSPD")
    print(f"  speeds: HSPD {hspd} pps" + (f" ({hspd / spd:.3f} deg/s)" if hspd is not None and spd else "")
          + f", LSPD {get('LSPD')} pps, ACC {get('ACC')} ms; pulse speed now (PS) {get('PS')}")
    print(f"  move mode: MM {get('MM')}" + {0: " (absolute)", 1: " (incremental)"}.get(get("MM"), ""))
    pol = get("POL")
    pol_bits = "" if pol is None else (f" (direction bit 1: {pol >> 1 & 1}, limit bit 4: {pol >> 4 & 1}, "
                                       f"home bit 5: {pol >> 5 & 1})")
    print(f"  POL {pol}{pol_bits}, IERR {get('IERR')}, RZ {get('RZ')}, HCA {get('HCA')}, RT {get('RT')}, "
          f"SL {get('SL')}, EDIO {get('EDIO')}, TOC {get('TOC')}")
    if driver is not None:
        microsteps, others = driver
        print(f"  driver (RR): DRVMS {microsteps}, DRVRC {others['DRVRC']} mA, DRVIC {others['DRVIC']} mA, "
              f"DRVIT {others['DRVIT']} cs")
        print(f"  scale: {scale_text(microsteps, spd)}")
    else:
        print(f"  driver parameters not read: {why_not}")
    if mst is not None:
        if mst & LIMIT_ERRORS:
            print("NOTE: a limit error is latched: moves are refused until 'clear'.")
        if mst & LIMIT_INPUTS and not mst & MOVING:
            print("NOTE: a limit input is on at rest, but the RM-5-110 has no limit switches: a polarity "
                  "problem; fix the limit polarity in QuickMotion. Moves are refused until then.")
    if get("EO") == 0:
        print("NOTE: the motor driver is off (EO=0): moves are refused until 'enable'.")
    if get("RT") == 1:
        print("NOTE: RT=1: replies carry a '#NN' prefix (removed here).")
    return 1 if failed else 0


def mode_move(nsc, args):
    microsteps, _others = nsc.read_driver()
    spd = nsc.steps_per_degree
    print(f"Scale: {scale_text(microsteps, spd)}")
    nsc.check_ready()
    start = nsc.number("PX")
    steps = round(args.degrees * spd)
    if steps == 0:
        raise Refused(f"{args.degrees:g} deg is less than one step")
    target = start + steps
    if abs(target) > CABLE_LIMIT_DEG * spd:
        raise Refused(f"the target {angle(target, spd)} is beyond +/-{CABLE_LIMIT_DEG:g} deg of the zero "
                      f"(sample wires); it is at {angle(start, spd)} now")
    if abs(target) > PX_SAFE:
        raise Refused(f"the target PX {target} is too close to the 28-bit counter's range")
    hspd, lspd, acc = speed_registers(args.speed, spd)
    expected = abs(steps) / hspd + 2 * acc / 1000
    print(f"\nMove {args.degrees:+g} deg at {args.speed:g} deg/s: {angle(start, spd)} -> {angle(target, spd)} "
          f"(absolute target, PX {start} -> {target}), about {expected:.1f} s.")
    print("Can the sample wires follow? Ctrl+C stops it at any point.")
    if not confirm(args.yes, "move"):
        print("Not moved.")
        return 1
    nsc.set_ok("ABS")
    mode = nsc.number("MM")
    if mode != 0:
        raise Refused(f"MM read back {mode}, not 0 (absolute)")
    acc_read = nsc.set_speeds(hspd, lspd, acc)
    nsc.check_ready()
    now = nsc.number("PX")
    if now != start:
        raise Refused(f"the position changed since the question (PX {start} -> {now})")
    timeout = 3 * (abs(steps) / hspd + 2 * acc_read / 1000) + 5
    nsc.start_motion(f"X{target}")
    elapsed, end, mst = nsc.wait_motion(timeout, "the move", expect_px=target)
    if mst & LIMIT_ERRORS:
        raise MotionError(f"it stopped with a limit error (MST {mst}: {describe_status(mst)})", True, end)
    if end != target:
        raise MotionError(f"it ended at PX {end} ({angle(end, spd)}), not the target {target} "
                          f"({end - target:+d} steps): steps lost, or the move was cut short", True, end)
    sign = "+" if steps > 0 else "-"
    print(f"\nMoved {angle(start, spd)} -> {angle(end, spd)} (PX {start} -> {end}, the target exactly) in "
          f"{elapsed:.1f} s (expected about {expected:.1f} s).")
    print(f"Please note which way the stage turned for this {sign} move (clockwise or counter-clockwise, seen "
          "from above): the RT-E angle scan will use that sign.")
    return 0


def mode_home(nsc, args):
    microsteps, _others = nsc.read_driver()
    spd = nsc.steps_per_degree
    print(f"Scale: {scale_text(microsteps, spd)}")
    nsc.check_ready()
    start, hca, rz = nsc.number("PX"), nsc.number("HCA"), nsc.number("RZ")
    hspd, lspd, acc = speed_registers(args.speed, spd)
    sign = 1 if args.direction == "+" else -1
    plan = HomePlan(start, sign, spd, hspd, lspd, acc, hca)
    plan.check_wires()
    print(f"\nHome search HL{args.direction} at {args.speed:g} deg/s from {angle(start, spd)}: it turns "
          f"{args.direction} until the home switch, at most one turn + {HOME_MARGIN_DEG:g} deg (it stops there "
          f"if it finds no switch), passes it by HCA = {hca} steps and comes back at low speed (RZ = {rz}). "
          f"All of it stays between {angle(plan.back, spd)} and {angle(plan.far, spd)} from the zero.")
    print("CABLES: the sample wires must be free to follow up to one turn in that direction.")
    print("AFTERWARDS the counter is 0 at the home switch: the zero, and the +/-360 deg wire limit of 'move', "
          "then count from the switch, not from where the wires hang free.")
    print(f"It is also stopped (STOP) if it has not finished within {plan.timeout:.0f} s; Ctrl+C stops it at any "
          "point.")
    if not confirm(args.yes, "search the home switch"):
        print("Not moved.")
        return 1
    acc_read = nsc.set_speeds(hspd, lspd, acc)
    plan = HomePlan(start, sign, spd, hspd, lspd, acc_read, hca)     # the ACC the controller kept
    plan.check_wires()
    nsc.check_ready()
    now = nsc.number("PX")
    if now != start:
        raise Refused(f"the position changed since the question (PX {start} -> {now})")
    watch = HomeWatch(plan, start, _now())
    nsc.home_watch = watch          # every report from here on says what PX counts from
    nsc.start_motion(f"HL{args.direction}")
    elapsed, end, mst = nsc.wait_motion(plan.timeout, "the home search", expect_px=0, watch=watch)
    if mst & LIMIT_ERRORS:
        raise MotionError(f"it stopped with a limit error (MST {mst}: {describe_status(mst)})", True, end)
    if end != 0:
        raise MotionError(f"the home search ended at PX {end}, not 0 (the manual: the counter is 0 at the "
                          "switch when HL ends)", True, end)
    print(f"\nHome search done in {elapsed:.1f} s: PX 0 at the home switch, home switch input "
          f"{'on' if mst & HOME_SWITCH else 'off (please note this)'}.")
    if watch.switch is None:
        print("No counter reset was seen on the way, so the home switch is within a few degrees of the old zero.")
    else:
        print("The zero, and the +/-360 deg wire limit of 'move', now count from the home switch.")
        print(old_zero_text(watch, end))
    print("(The counter is runtime only: lost when the controller is powered off.)")
    return 0


def mode_stop(nsc, args):
    nsc.motion_may_run = True       # whatever turns it (QuickMotion, a run cut off)
    with nsc.hold_ctrl_c():
        stopped, px, notes = nsc.stop_motion()
        if notes:
            print("  (" + "; ".join(notes) + ")")
        if not stopped:
            print("\nMOTION STATE UNKNOWN: it could not be confirmed stopped. CHECK THE STAGE (switch the "
                  "controller's 24 V off if it is still turning).")
            return 3
    try:
        nsc.read_driver()
    except NscError as error:
        print(f"  (no angle: {error})")
    print(f"\nStopped (MST read back): PX {px} ({angle(px, nsc.steps_per_degree)}).")
    return 0


def mode_zero(nsc, args):
    mst = nsc.status()
    if mst & MOVING:
        raise Refused("it is moving; stop it first")
    try:
        nsc.read_driver()
    except NscError as error:
        print(f"  (no angle: {error})")
    start = nsc.number("PX")
    # Every move's wire check counts from this zero, so it is asked like enable (user, 2026-10-13).
    where = f"{angle(start, nsc.steps_per_degree)} (PX {start})" if nsc.steps_per_degree else f"PX {start}"
    print(f"\nThe stage is at {where} from the present zero. Calling this 0 deg moves the +/-360 deg limit of "
          "every move with it: the sample wires must hang free here.")
    if not confirm(args.yes, "call this position 0 deg"):
        print("Zero not changed.")
        return 1
    mst = nsc.status()
    if mst & MOVING:
        raise Refused("it is moving; stop it first")
    if nsc.number("PX") != start:
        raise Refused("the position changed since the question")
    nsc.set_ok("PX=0")
    now = nsc.number("PX")
    if now != 0:
        raise NscError(f"PX read back {now} after PX=0")
    print(f"\nZero set here: PX {start} ({angle(start, nsc.steps_per_degree)}) -> 0. Runtime only: lost when "
          "the controller is powered off, kept across a USB reconnect.")
    return 0


def mode_clear(nsc, args):
    before = nsc.status()
    nsc.set_ok("CLR")
    after = nsc.status()
    if after & LIMIT_ERRORS:
        print(f"\nThe limit error is still latched after CLR (MST {after}: {describe_status(after)}).")
        return 1
    print("\nLimit error cleared (MST read back)." if before & LIMIT_ERRORS
          else "\nNo limit error was latched; CLR sent anyway (MST read back).")
    if after & LIMIT_INPUTS:
        print("NOTE: a limit input is on at rest, but the RM-5-110 has no limit switches: a polarity problem; "
              "fix the limit polarity in QuickMotion. Moves stay refused until then.")
    return 0


def mode_enable(nsc, args):
    eo = nsc.number("EO")
    nsc.status()
    if eo == 1:
        print("\nThe motor driver is already on (EO=1).")
        return 0
    print("\nSwitch the motor driver on (EO=1): the motor is energized and holds the stage.")
    if not confirm(args.yes, "switch it on"):
        print("Not switched on.")
        return 1
    nsc.set_ok("EO=1")
    eo = nsc.number("EO")
    if eo != 1:
        print(f"EO read back {eo}, not 1.")
        return 1
    print("Motor driver on (EO=1, read back).")
    return 0


def mode_disable(nsc, args):
    mst = nsc.status()
    if mst & MOVING:
        raise Refused("it is moving; stop it first (switching the driver off mid-move loses the position)")
    nsc.set_ok("EO=0")
    eo = nsc.number("EO")
    if eo != 0:
        print(f"EO read back {eo}, not 0.")
        return 1
    print("\nMotor driver off (EO=0, read back): the motor is not energized; moves are refused until 'enable'.")
    return 0


MODES = {"info": mode_info, "move": mode_move, "stop": mode_stop, "zero": mode_zero, "clear": mode_clear,
         "enable": mode_enable, "disable": mode_disable, "home": mode_home}


def report_stop(why, stopped, px, notes, nsc):
    """Print where a stop left the stage (and, after a home search, what that
    angle counts from); returns the exit status, 1 or 3."""
    spd = nsc.steps_per_degree
    if notes:
        print("  (" + "; ".join(notes) + ")")
    if stopped:
        where = angle(px, spd)
        if px is not None and spd:
            where += f" (PX {px})"
        print(f"{why}; the motor is stopped (MST read back) at {where}.")
        code = 1
    else:
        print(f"{why}; MOTION STATE UNKNOWN: it could not be confirmed stopped. CHECK THE STAGE (switch the "
              "controller's 24 V off if it is still turning).")
        px, code = None, 3
    note = home_note(nsc, px)
    if note:
        print(note)
    return code


def run_mode(nsc, args):
    try:
        return MODES[args.mode](nsc, args)
    except Refused as error:
        print(f"\nRefused: {error}. Nothing moved.")
        return 1
    except MotionError as error:
        with nsc.hold_ctrl_c():
            return report_stop(f"\n{error}", error.stopped, error.px, error.notes, nsc)
    except NscError as error:
        if nsc.motion_may_run:
            with nsc.hold_ctrl_c():
                print(f"\n{error}: STOP")
                stopped, px, notes = nsc.stop_motion()
                return report_stop("Stopped after the failure", stopped, px, notes, nsc)
        print(f"\nFailed: {error}")
        return 1


def after_interrupt(nsc, args):
    if nsc is None or not nsc.identified:
        print("\nStopped (Ctrl+C) before the controller was confirmed; nothing else was sent.")
        return 3 if args.mode == "stop" else 1
    with nsc.hold_ctrl_c():         # a second Ctrl+C asks for ABORT; it cannot cut the report short
        if not nsc.motion_may_run:
            if nsc.motion_sent or nsc.stop_finished:
                print("\nStopped (Ctrl+C); no move is running (it was read back not moving).")
                note = home_note(nsc, None)
                if note:
                    print(note)
            else:
                print("\nStopped (Ctrl+C); this run had not started a move.")
            return 1
        print("\nStopped (Ctrl+C): STOP")
        stopped, px, notes = nsc.stop_motion()
        return report_stop("Stopped (Ctrl+C)", stopped, px, notes, nsc)


def cut_short_code(args, nsc):
    """The exit status when a second Ctrl+C lands before after_interrupt holds
    Ctrl+C.  close() then stops a move that may still run and reports it, so
    only what it leaves out is decided here: a stop that ended without being
    confirmed (close does not repeat it), and stop mode before its stop ran,
    are 3 (user, 2026-10-13)."""
    if nsc is not None and nsc.motion_may_run and nsc.stop_finished:
        return 3
    if args.mode == "stop" and (nsc is None or not (nsc.motion_may_run or nsc.stop_finished)):
        return 3
    return 1


def close_and_report(nsc):
    """Close the controller; if close() had to stop a move (a run cut off in an
    unforeseen way), report that stop like any other.  Returns its exit status,
    or None when nothing had to be stopped.  Run again after a Ctrl+C before
    its hold, it gives the status it already found."""
    with nsc.hold_ctrl_c():
        if nsc.handle is not None:
            outcome = nsc.close()
            if outcome is not None:
                stopped, px, notes = outcome
                nsc.closing_code = report_stop("Stopped on closing", stopped, px, notes, nsc)
    return nsc.closing_code


def parse_arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", nargs="?", default="info", choices=tuple(MODES))
    parser.add_argument("--degrees", type=float, help="move: relative turn, -360 to 360 (not 0)")
    parser.add_argument("--speed", type=float, help=f"move/home: deg/s (default {DEFAULT_SPEED:g}, at most "
                                                    f"{MAX_SPEED:g})")
    parser.add_argument("--direction", choices=("+", "-"), help="home: the search direction")
    parser.add_argument("--serial", help="the controller's USB serial (with several Performax devices)")
    parser.add_argument("--dll-dir", help=f"the folder with the 64-bit {DLL_NAME} and {SIUSB_NAME}")
    parser.add_argument("--yes", action="store_true", help="do not ask")
    parser.add_argument("--dry-run", action="store_true", help="a simulated controller; nothing is sent")
    args = parser.parse_args(argv)
    if args.mode == "move":
        if args.degrees is None:
            parser.error("move needs --degrees (relative, -360 to 360, not 0)")
        if not (math.isfinite(args.degrees) and 0 < abs(args.degrees) <= MAX_MOVE_DEG):
            parser.error(f"--degrees must be within -{MAX_MOVE_DEG:g} to {MAX_MOVE_DEG:g} and not 0")
    elif args.degrees is not None:
        parser.error("--degrees is for move only")
    if args.mode == "home":
        if args.direction is None:
            parser.error("home needs --direction + or -")
    elif args.direction is not None:
        parser.error("--direction is for home only")
    if args.speed is None:
        args.speed = DEFAULT_SPEED
    elif args.mode not in ("move", "home"):
        parser.error("--speed is for move and home only")
    elif not (math.isfinite(args.speed) and MIN_SPEED <= args.speed <= MAX_SPEED):
        parser.error(f"--speed must be within {MIN_SPEED:g} - {MAX_SPEED:g} deg/s")
    if args.serial is not None and not args.serial.strip():
        parser.error("--serial is empty")
    if args.dll_dir is not None and not args.dry_run:
        folder = os.path.abspath(args.dll_dir)
        if not os.path.isfile(os.path.join(folder, DLL_NAME)):
            parser.error(f"{DLL_NAME} is not in --dll-dir {folder}")
    return args


DRY_RUN_START_PX = -200000      # the simulated stage starts at -20 deg, so a dry "home --direction +" can run


def open_dll(args):
    if args.dry_run:
        print("(dry run: a simulated NSC-A1 stands in for PerformaxCom.dll; nothing is sent to any hardware; "
              "its stage starts at -20 deg)")
        return SimulatedPerformax(SimulatedNSC(px=DRY_RUN_START_PX)), "simulated"
    return load_performax(args.dll_dir)


def run_session(args, session):
    """Open the controller, confirm it and run the mode.  The NSC goes into
    ``session`` as soon as it exists, for Ctrl+C and for closing."""
    nothing = 3 if args.mode == "stop" else 1      # stop mode: not stopped means the state is unknown
    try:
        dll, where = open_dll(args)
    except PerformaxError as error:
        print(f"{error}\nNothing was sent.")
        return nothing
    print(f"DLL: {where}")
    nsc = NSC(dll, allow_homing=args.mode == "home")
    session.append(nsc)
    try:
        index = nsc.choose_device(args.serial)
    except NscError as error:
        print(f"{error}\nNothing was sent.")
        return nothing
    try:
        nsc.open(index)
        identity, version = nsc.identify()
    except NscError as error:
        print(f"{error}\nNothing else was sent.")
        return nothing
    print(f"NSC-A1 confirmed: {identity}, firmware {version}.")
    return run_mode(nsc, args)


def main(argv=None):
    args = parse_arguments(argv)
    session, code, closing = [], None, None
    try:
        try:
            code = run_session(args, session)
        except KeyboardInterrupt:
            code = after_interrupt(session[0] if session else None, args)
    except KeyboardInterrupt:
        # A second Ctrl+C a few ms after the first can land before after_interrupt
        # holds Ctrl+C; close_and_report below still stops a move that may run
        # (user, 2026-10-13).
        code = max(code or 0, cut_short_code(args, session[0] if session else None))
        try:
            print("\nCtrl+C again: closing (a move that may still run is stopped first).")
        except KeyboardInterrupt:
            pass
    finally:
        while session:      # a Ctrl+C before close_and_report holds Ctrl+C: run it again
            try:
                closing = close_and_report(session[0])
                break
            except KeyboardInterrupt:
                pass
    return code if closing is None else max(code, closing)


if __name__ == "__main__":
    sys.exit(main())
