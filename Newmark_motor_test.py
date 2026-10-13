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
    py Newmark_motor_test.py zero                  call this position 0 deg (PX=0; lost at power-off)
    py Newmark_motor_test.py clear                 clear a latched limit error (CLR)
    py Newmark_motor_test.py enable                motor driver on (EO=1; asks first)
    py Newmark_motor_test.py disable               motor driver off (EO=0)
    py Newmark_motor_test.py home --direction +    find the home switch (HL+; asks first)
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
confirmed stopped; 2 a usage error (nothing sent); 3 the motion state is
unknown: check the stage.

SAFETY
  - Sample wires: every target stays within +/-360 deg of the zero (PX = 0),
    and moves are absolute, so 350 -> 0 goes back -350 deg, never across
    360.  Before the first move, set the zero where the wires hang free
    ("zero").  The zero is lost when the controller is powered off.
  - "home" turns up to a full turn in its direction: check the wires first.
  - Ctrl+C at any point sends STOP if a move may be running, waits until it
    has stopped (ABORT if STOP does not stop it) and says where it stopped.
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
MAX_MOVE_DEG = 360.0        # one relative move
CABLE_LIMIT_DEG = 360.0     # every target within +/-360 deg of the zero (sample wires)
PX_LIMIT = 2 ** 27 - 1      # PX is a 28-bit signed number
PX_SAFE = PX_LIMIT // 4     # targets stay far inside it
POLL_S = 0.05
REPORT_EVERY_S = 1.0
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
NUMERIC = re.compile(r"(?:X|PX=)-?[0-9]{1,9}|(?:HSPD|LSPD|ACC)=[0-9]{1,7}")
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


def check_allowed(command, homing=False):
    """Raise CommandNotAllowed unless the command is on the list above."""
    if (command in READ_ONLY or command in ACTIONS or (homing and command in HOMING)
            or NUMERIC.fullmatch(command)):
        return
    raise CommandNotAllowed(f"{command!r} is not on this script's list of allowed commands; it was not sent")


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


def explain_load_error(path, error):
    bits = ctypes.sizeof(ctypes.c_void_p) * 8
    code = getattr(error, "winerror", None)
    if code == 193 or "193" in str(error):
        return (f"Could not load {path} (WinError 193): it is not a {bits}-bit DLL. This Python is "
                f"{bits}-bit, so {DLL_NAME} and {SIUSB_NAME} must both be the 64-bit ones (Arcus "
                f"\"USB 64-bit DLL v2.06\"), side by side.")
    if code == 126 or "126" in str(error):
        return (f"Could not load {path} (WinError 126): it, or the {SIUSB_NAME} it needs, was not found. "
                f"Put the 64-bit {DLL_NAME} and {SIUSB_NAME} together in this script's folder or in --dll-dir.")
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
        self.stop_finished = False      # a stop sequence ran to its end (stopped or not)
        self.steps_per_degree = None
        self._no_flush_said = False

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
        """STOP first if a move may still be running (bounded), then close the handle."""
        if self.handle is None:
            return
        try:
            if self.motion_may_run and not self.stop_finished:
                self.echo("Closing while a move may be running: STOP first.")
                self.stop_motion()
        except BaseException:
            pass
        finally:
            try:
                self.echo("> (close)")
                self.dll.fnPerformaxComClose(self.handle)
            except BaseException:
                pass
            self.handle = None

    # ---- one command ---------------------------------------------------------
    def _transact(self, command, quiet=False):
        """Send one allowed command and return its reply (never repeated)."""
        check_allowed(command, self.allow_homing)
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
        if "SDE" not in identity:
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
        if "SDE" not in identity:
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
        self.stop_finished = False
        self.set_ok(command)

    def wait_motion(self, timeout_s, what="the move"):
        """Poll MST every 50 ms until it is not moving; past the time-out, or on a
        communication failure, STOP and raise.  Returns (seconds, PX, MST)."""
        spd = self.steps_per_degree
        started = _now()
        next_line, last, reason = started, None, None
        try:
            while True:
                mst = self.status(quiet=True)
                now = _now()
                if not mst & MOVING:
                    break
                if now - started > timeout_s:
                    reason = f"{what} did not end within {timeout_s:.0f} s (3 x the expected time + 5 s)"
                    break
                if mst != last or now >= next_line:
                    px = self.number("PX", quiet=True)
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

    def _wait_until_still(self, wait_s, notes):
        """(True, PX) once MST shows it not moving; (False, None) after wait_s or a Ctrl+C."""
        deadline = _now() + wait_s
        while True:
            try:
                mst = self.status(quiet=True)
                if not mst & MOVING:
                    self.echo(f"  MST {mst} ({describe_status(mst)})")
                    break
            except KeyboardInterrupt:
                notes.append("Ctrl+C while waiting for the stop")
                return False, None
            except Exception as error:
                text = describe_error(error)
                if text not in notes:
                    notes.append(text)
            if _now() >= deadline:
                return False, None
            try:
                _sleep(POLL_S)
            except KeyboardInterrupt:
                notes.append("Ctrl+C while waiting for the stop")
                return False, None
        self.motion_may_run = False
        try:
            return True, self.number("PX")
        except BaseException as error:
            notes.append(f"PX could not be read: {describe_error(error)}")
            return True, None

    def stop_motion(self, wait_s=STOP_WAIT_S):
        """STOP (decelerated); wait until it is not moving; ABORT if STOP did not
        stop it.  Returns (stopped, PX, notes); every step is tried even after a
        Ctrl+C, as stopping is the safe state."""
        self.stop_finished = False
        notes = []
        self._send_stop("STOP", notes)
        stopped, px = self._wait_until_still(wait_s, notes)
        if not stopped:
            self.echo("Not stopped after STOP: ABORT")
            self._send_stop("ABORT", notes)
            stopped, px = self._wait_until_still(ABORT_WAIT_S, notes)
        self.stop_finished = True       # close() repeats it only if this was cut short
        return stopped, px, notes


# ---- --dry-run: a simulated controller --------------------------------------------
class SimulatedNSC:
    """An NSC-A1 that answers as its manual describes, with a stage that turns in
    (simulated) time: DRVMS 250, so 10000 steps/deg; home switch at +37.5 deg."""

    def __init__(self, serials=("SIM-NSC-A1",), identity="Ace-Series-SDE"):
        self.serials = list(serials)
        self.identity = identity
        self.version, self.name = "V231BL", "SDE01"
        self.handle = None
        self.calls = []             # DLL-level calls: ("timeouts", r, w), ("open", i), ("flush",), ("close", h)
        self.received = []          # every command, in order
        self.px, self.eo, self.mm = 0, 1, 0
        self.hspd, self.lspd, self.acc = 1000, 100, 300
        self.inputs = 0             # MST input bits (3 home, 4/5 limit inputs, 8-10)
        self.latched = 0            # MST bits 6/7
        self.registers = {"POL": 0, "IERR": 0, "RZ": 0, "HCA": 0, "RT": 0, "SL": 0, "EDIO": 0, "TOC": 0}
        self.driver = {"DRVMS": 250, "DRVRC": 2000, "DRVIC": 500, "DRVIT": 50}
        self.driver_read = False
        self.silent_until = -math.inf   # RR: no communication for 2 s
        self.home_px = 375000           # the home switch, in counter steps
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
            elif command == "HL+":
                self._start(self.px + ((self.home_px - self.px) % self.turn() or self.turn()), now, homing=True)
            else:
                self._start(self.px - ((self.px - self.home_px) % self.turn() or self.turn()), now, homing=True)
            return "OK"
        return "?" + command

    def _start(self, target, now, homing=False):
        duration = abs(target - self.px) / max(self.hspd, 1) + self.acc / 1000 + (0.5 if homing else 0.0)
        self.motion = {"from": self.px, "to": target, "t0": now, "t1": now + duration,
                       "homing": homing, "stopping": False}

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
        if self.motion is not None and self._finished(now):
            motion, self.motion = self.motion, None
            if motion["homing"] and not motion["stopping"]:
                self.px, self.home_px = 0, 0    # HL: the counter becomes 0 at the switch
            else:
                self.px = self._end_position(motion)

    def _status(self, now):
        bits = self.inputs | self.latched
        motion = self.motion
        if motion is not None:
            ramp = self.acc / 1000
            bits |= 2 if now - motion["t0"] < ramp else 4 if motion["t1"] - now < ramp else 1
        elif (self.px - self.home_px) % self.turn() == 0:
            bits |= HOME_SWITCH
        return bits

    def _stop(self, command, now):
        motion = self.motion
        if motion is None:
            return
        if command == "ABORT":
            self.px, self.motion = self._position(now), None
            return
        end = now + min(max(motion["t1"] - now, 0.0), self.acc / 1000)
        here, there = self._position(now), self._position(end)
        self.motion = dict(motion, **{"from": here, "to": there, "t0": now, "t1": end, "stopping": True})


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
    elapsed, end, mst = nsc.wait_motion(timeout, "the move")
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
    turn = 360 * spd
    sign = 1 if args.direction == "+" else -1
    expected = (turn + 2 * abs(hca)) / hspd + abs(hca) / lspd + 4 * acc / 1000
    timeout = 3 * expected + 5
    print(f"\nHome search HL{args.direction} at {args.speed:g} deg/s from {angle(start, spd)}: it turns "
          f"{args.direction} until the home switch, up to one full turn (to {angle(start + sign * turn, spd)} "
          f"from the zero), passes it by HCA = {hca} steps and comes back at low speed; the counter becomes 0 at "
          f"the switch, which replaces the zero (RZ = {rz}).")
    print("CABLES: the sample wires must be free to follow up to a full turn in that direction.")
    print(f"It is stopped (STOP) if it has not finished within {timeout:.0f} s; Ctrl+C stops it at any point.")
    if not confirm(args.yes, "search the home switch"):
        print("Not moved.")
        return 1
    nsc.set_speeds(hspd, lspd, acc)
    nsc.check_ready()
    now = nsc.number("PX")
    if now != start:
        raise Refused(f"the position changed since the question (PX {start} -> {now})")
    nsc.start_motion(f"HL{args.direction}")
    elapsed, end, mst = nsc.wait_motion(timeout, "the home search")
    if mst & LIMIT_ERRORS:
        raise MotionError(f"it stopped with a limit error (MST {mst}: {describe_status(mst)})", True, end)
    print(f"\nHome search done in {elapsed:.1f} s: PX {end} ({angle(end, spd)}), home switch input "
          f"{'on' if mst & HOME_SWITCH else 'off'}.")
    if end != 0:
        print("NOTE: the counter is not 0 after HL (the manual: 0 at the switch); please note this.")
    print("The zero is now the home switch (runtime only: lost when the controller is powered off).")
    return 0


def mode_stop(nsc, args):
    nsc.motion_may_run = True       # whatever turns it (QuickMotion, a run cut off)
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


def report_stop(why, stopped, px, notes, steps_per_degree):
    if notes:
        print("  (" + "; ".join(notes) + ")")
    if stopped:
        where = angle(px, steps_per_degree)
        if px is not None and steps_per_degree:
            where += f" (PX {px})"
        print(f"{why}; the motor is stopped (MST read back) at {where}.")
        return 1
    print(f"{why}; MOTION STATE UNKNOWN: it could not be confirmed stopped. CHECK THE STAGE (switch the "
          "controller's 24 V off if it is still turning).")
    return 3


def run_mode(nsc, args):
    try:
        return MODES[args.mode](nsc, args)
    except Refused as error:
        print(f"\nRefused: {error}. Nothing moved.")
        return 1
    except MotionError as error:
        return report_stop(f"\n{error}", error.stopped, error.px, error.notes, nsc.steps_per_degree)
    except NscError as error:
        if nsc.motion_may_run:
            print(f"\n{error}: STOP")
            stopped, px, notes = nsc.stop_motion()
            return report_stop("Stopped after the failure", stopped, px, notes, nsc.steps_per_degree)
        print(f"\nFailed: {error}")
        return 1


def after_interrupt(nsc, args):
    if nsc is None or not nsc.identified:
        print("\nStopped (Ctrl+C) before the controller was confirmed; nothing else was sent.")
        return 3 if args.mode == "stop" else 1
    if not nsc.motion_may_run:
        print("\nStopped (Ctrl+C); this run had not started a move.")
        return 1
    print("\nStopped (Ctrl+C): STOP")
    stopped, px, notes = nsc.stop_motion()
    return report_stop("Stopped (Ctrl+C)", stopped, px, notes, nsc.steps_per_degree)


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
    return args


def open_dll(args):
    if args.dry_run:
        print("(dry run: a simulated NSC-A1 stands in for PerformaxCom.dll; nothing is sent to any hardware)")
        return SimulatedPerformax(), "simulated"
    return load_performax(args.dll_dir)


def main(argv=None):
    args = parse_arguments(argv)
    nsc = None
    try:
        try:
            dll, where = open_dll(args)
        except PerformaxError as error:
            print(f"{error}\nNothing was sent.")
            return 3 if args.mode == "stop" else 1
        print(f"DLL: {where}")
        nsc = NSC(dll, allow_homing=args.mode == "home")
        try:
            index = nsc.choose_device(args.serial)
        except NscError as error:
            print(f"{error}\nNothing was sent.")
            return 3 if args.mode == "stop" else 1
        try:
            nsc.open(index)
            identity, version = nsc.identify()
        except NscError as error:
            print(f"{error}\nNothing else was sent.")
            return 3 if args.mode == "stop" else 1
        print(f"NSC-A1 confirmed: {identity}, firmware {version}.")
        return run_mode(nsc, args)
    except KeyboardInterrupt:
        return after_interrupt(nsc, args)
    finally:
        if nsc is not None:
            nsc.close()


if __name__ == "__main__":
    sys.exit(main())
