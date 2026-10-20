"""Avtech AVR-E3-B pulse generator: a standalone test over GPIB (user, 2026-10-13).

Needs only PyVISA (py -m pip install pyvisa) and NI-VISA.  One pulse uses the
same sequence as the CIDWM page of Transport_all_NH_dev.py (reviewed twice),
from Avtech's manuals (AVR-E2,E3-B sn14486 and ed9; programming manual for
"-B" instruments, firmware 5.07):

    device clear, *CLS, trigger:source hold, output off (errors and output?
    checked), pulse:delay 0, pulse:width and volt:ampl (each read back),
    output on, trigger:source immediate (one pulse), trigger:source hold,
    then hold and output off again, read back, and the overload state.

Usage (from a terminal in this folder):

    py Avtech_pulse_test.py                                  read only: identity and settings
    py Avtech_pulse_test.py off                              trigger on hold, output off (read back)
    py Avtech_pulse_test.py pulse --amplitude 10 --width 10  one pulse: +10 V, 10 ns (asks first)
    py Avtech_pulse_test.py pulse --amplitude=-10 --width 10 one pulse: -10 V (-N units; note the "=")
    py Avtech_pulse_test.py pulse --amplitude 10 --width 10 --dry-run
                                                             the same on a simulated generator

    --resource GPIB0::8::INSTR   the VISA resource (Avtech's factory GPIB address is 8)
    --yes                        do not ask before the pulse
    --dry-run                    a simulated generator: prints every command, sends nothing

Every command sent and every reply is printed ("> " sent, "< " received).
Before the pulse question, pulse mode puts the trigger on hold and the output
off (read back).  Ctrl+C at any point still ends with the output off, or with
"CHECK THE GENERATOR"; before the identity is confirmed nothing else is sent.

Exit status: 0 done (an overload is printed); 1 nothing fired (refused, not
confirmed, not an Avtech, no connection, stopped) or a failed pulse with the
output read back off; 2 a usage error (nothing sent); 3 the output state is
unknown: check the generator.

SAFETY
  - OUT needs a 50 ohm load; up to 100 V.  The generator cannot detect a
    missing load.  Change cables only with the output off.
  - First test into a 50 ohm load with an oscilloscope, not the sample.
  - The polarity is the unit's, from its *IDN? model (-P, -N or -PN; the
    lab's AVR-E3-B-W3-PN-OT gives both): a pulse of a sign the unit cannot
    give is refused before anything is fired.
  - Amplitude above 0 and at most 100 V; width 1 ns - 5 us.  Below 10 V
    Avtech advises an external attenuator.
"""
import argparse
import math
import re
import sys
import time

DEFAULT_RESOURCE = "GPIB0::8::INSTR"
MAX_AMPLITUDE_V = 100.0
MIN_WIDTH_S = 1e-9
MAX_WIDTH_S = 5e-6
OVERLOAD_ERROR = 1001  # "Device-specific error; Overload condition detected!"


class PulseError(RuntimeError):
    """A pulse that failed; ``fired`` says whether the trigger was sent."""

    def __init__(self, message, fired=False):
        super().__init__(message)
        self.fired = fired


class Avtech:
    """The generator's commands, printed as they are sent."""

    def __init__(self, instrument, echo=print):
        self.instrument = instrument
        self.echo = echo
        self.identity = ""

    # ---- I/O -------------------------------------------------------------
    def write(self, command):
        self.echo(f"> {command}")
        self.instrument.write(command)

    def query(self, command):
        self.echo(f"> {command}")
        reply = str(self.instrument.query(command)).strip()
        self.echo(f"< {reply}")
        return reply

    def clear(self):
        self.echo("> (device clear)")
        self.instrument.clear()

    def number(self, command):
        reply = self.query(command)
        try:
            value = float(reply)
        except ValueError as error:
            raise RuntimeError(f"{command} returned {reply!r}") from error
        if not math.isfinite(value):
            raise RuntimeError(f"{command} returned {reply!r}")
        return value

    def errors(self, found=None):
        """The error queue, read until empty (at most 32), into ``found``."""
        found = [] if found is None else found
        for _ in range(32):
            reply = self.query("syst:err?")
            try:
                code = int(float(reply.split(",", 1)[0]))
            except ValueError:
                found.append((-1, reply))
                continue
            if code == 0:
                break
            found.append((code, reply))
        return found

    @property
    def polarities(self):
        """(1,), (-1,), (1, -1) from the model field of *IDN? (-P, -N, -PN); None if it does not say."""
        fields = self.identity.split(",")
        model = fields[1].strip().upper() if len(fields) > 1 else ""
        match = re.search(r"-(PN|P|N)(?=-|$)", model)
        return None if match is None else {"PN": (1, -1), "P": (1,), "N": (-1,)}[match.group(1)]

    # ---- safe states -----------------------------------------------------
    def off_problem(self):
        """Hold and output off, read back; None when off, else what went wrong."""
        failures = []

        def clear():
            try:
                self.clear()
            except BaseException as error:  # Ctrl+C included, as the program's controller
                failures.append(f"device clear: {error!r}")

        def send(command):
            try:
                self.write(command)
                return True
            except BaseException as error:
                failures.append(f"{command}: {error!r}")
                return False

        clear()
        for command in ("trigger:source hold", "output off"):
            if not send(command):
                clear()
                send(command)
        for attempt in range(2):
            try:
                if self.number("output?") == 0:
                    if failures:
                        self.echo("  (read back off, after: " + "; ".join(failures) + ")")
                    return None
                failures.append("the output did not read back off")
            except BaseException as error:
                failures.append(f"output? failed: {error!r}")
            if attempt == 0:
                clear()
                send("output off")
        return "; ".join(failures)

    # ---- one pulse -------------------------------------------------------
    def single_pulse(self, amplitude_v, width_s):
        """One pulse; returns (amplitude read back, width read back, overload)."""
        sign = 1 if amplitude_v > 0 else -1
        allowed = self.polarities
        if allowed is not None and sign not in allowed:
            raise PulseError(f"this unit gives {'positive' if allowed == (1,) else 'negative'} "
                             "pulses only (its polarity option); no pulse fired")
        failure = None
        fired = trigger_sent = False
        amplitude_read = width_read = math.nan
        after, others = [], []
        try:
            self.clear()
            self.write("*CLS")
            self.write("trigger:source hold")
            self.write("output off")
            refused = self.errors()
            if refused:
                raise RuntimeError("refused hold or output off: " + "; ".join(text for _c, text in refused))
            if self.number("output?") != 0:
                raise RuntimeError("the output did not read back off before the pulse")
            self.write("pulse:delay 0")
            if not abs(self.number("pulse:delay?")) <= 1e-9:
                raise RuntimeError("it did not take a delay of 0")
            self.write(f"pulse:width {width_s:.6g}")
            width_read = self.number("pulse:width?")
            if not abs(width_read - width_s) <= max(0.05 * width_s, 0.2e-9):
                raise RuntimeError(f"it took a width of {width_read:.4g} s for {width_s:.4g} s")
            self.write(f"volt:ampl {amplitude_v:.6g}")
            amplitude_read = self.number("volt:ampl?")
            if not (amplitude_read * sign > 0
                    and abs(amplitude_read - amplitude_v) <= max(0.02 * abs(amplitude_v), 0.5)):
                raise RuntimeError(f"it took {amplitude_read:+g} V for {amplitude_v:+g} V (its polarity option?)")
            errors = self.errors()
            if errors:
                raise RuntimeError("it reported " + "; ".join(text for _c, text in errors))
            self.write("output on")
            if self.number("output?") != 1:
                raise RuntimeError("the output did not switch on")
            fired = True
            self.write("trigger:source immediate")  # the pulse
            trigger_sent = True
            self.write("trigger:source hold")
            self.errors(after)
        except BaseException as error:  # Ctrl+C included: the output still goes off below
            failure = error
        problem = self.off_problem()
        try:
            self.errors(after)
        except BaseException as error:
            others.append(f"its error queue could not be read: {error!r}")
        overload = any(code == OVERLOAD_ERROR for code, _text in after)
        others += [text for code, text in after if code != OVERLOAD_ERROR]
        try:
            overload = overload or self.number("output:protection:tripped?") != 0
        except BaseException as error:
            others.append(f"its overload state could not be read: {error!r}")
            overload = True if overload else None
        parts = ([("stopped (Ctrl+C)" if isinstance(failure, KeyboardInterrupt)
                   else str(failure) or type(failure).__name__)]
                 if failure is not None else [])
        parts += [f"it reported {text}" for text in others]
        if problem is not None:
            parts.append(f"{problem}; CHECK THE GENERATOR")
        if parts:
            if overload:
                parts.append("OVERLOAD: output protection tripped (short or too-low load impedance)")
            if trigger_sent:
                prefix = "pulse fired, then: "
            elif fired:
                prefix = "the trigger may have been sent (a pulse may have fired), then: "
            else:
                prefix = "no pulse fired: "
            error = PulseError(prefix + "; ".join(parts), fired=fired)
            error.off_unknown = problem is not None
            raise error from failure
        return amplitude_read, width_read, overload


class SimulatedAvtech:
    """--dry-run: answers as Avtech's manuals describe; a -P unit."""

    def __init__(self):
        self.trigger, self.output, self.width, self.amplitude, self.delay = "internal", 0, 1e-8, 10.0, 0.0
        self.errors, self.pulses = [], []

    def clear(self):
        pass

    def write(self, command):
        word, _, value = command.lower().partition(" ")
        if word == "*cls":
            self.errors.clear()
        elif word == "trigger:source":
            if value == "immediate" and self.output:
                self.pulses.append((self.amplitude, self.width))
            self.trigger = value
        elif word == "output":
            self.output = 1 if value in ("on", "1") else 0
        elif word == "pulse:width":
            self.width = float(value)
        elif word == "pulse:delay":
            self.delay = float(value)
        elif word == "volt:ampl":
            if float(value) > 0:
                self.amplitude = float(value)
            else:
                self.errors.append('-222,"Data out of range"')

    def query(self, command):
        command = command.lower()
        replies = {"*idn?": "AVTECH ELECTROSYSTEMS,AVR-E3-B-P-W3,SIMULATED,5.07",
                   "output?": str(self.output), "volt:ampl?": f"{self.amplitude:.4e}",
                   "pulse:width?": f"{self.width:.4e}", "pulse:delay?": f"{self.delay:.4e}",
                   "freq?": "1.0000e+03", "output:protection:tripped?": "0"}
        if command == "syst:err?":
            return self.errors.pop(0) if self.errors else '0,"No error"'
        return replies[command]

    def close(self):
        pass


def open_generator(resource, dry_run):
    if dry_run:
        print(f"(dry run: a simulated -P generator stands in for {resource})")
        return Avtech(SimulatedAvtech()), None
    import pyvisa
    manager = pyvisa.ResourceManager()
    instrument = manager.open_resource(resource)
    instrument.timeout = 5000  # ms
    instrument.write_termination = "\n"
    instrument.read_termination = "\n"
    try:
        instrument.query_delay = 0.05
    except (AttributeError, ValueError):
        pass
    return Avtech(instrument), manager


def read_only(avtech):
    """Settings and state; nothing is set."""
    for label, command in (("output (1 = on)", "output?"), ("amplitude V", "volt:ampl?"),
                           ("width s", "pulse:width?"), ("delay s", "pulse:delay?"),
                           ("frequency Hz", "freq?"), ("overload tripped", "output:protection:tripped?")):
        try:
            print(f"  {label}: {avtech.query(command)}")
        except Exception as error:
            print(f"  {label}: could not be read ({error})")
    try:
        errors = avtech.errors()
        print("  error queue: " + ("empty" if not errors else "; ".join(text for _c, text in errors)))
    except Exception as error:
        print(f"  error queue: could not be read ({error})")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", nargs="?", default="info", choices=("info", "off", "pulse"))
    parser.add_argument("--resource", default=DEFAULT_RESOURCE)
    parser.add_argument("--amplitude", type=float, help="volts, signed (-P units: positive)")
    parser.add_argument("--width", type=float, help="nanoseconds, 1 - 5000")
    parser.add_argument("--yes", action="store_true", help="do not ask before the pulse")
    parser.add_argument("--dry-run", action="store_true", help="a simulated generator; nothing is sent")
    args = parser.parse_args(argv)

    if args.mode == "pulse":
        if args.amplitude is None or args.width is None:
            parser.error("pulse needs --amplitude (V) and --width (ns)")
        width_s = args.width * 1e-9
        if not (math.isfinite(args.amplitude) and 0 < abs(args.amplitude) <= MAX_AMPLITUDE_V):
            parser.error(f"--amplitude must be above 0 and at most {MAX_AMPLITUDE_V:g} V")
        if not (math.isfinite(width_s) and MIN_WIDTH_S * 0.999 <= width_s <= MAX_WIDTH_S * 1.001):
            parser.error("--width must be within 1 - 5000 ns")

    try:
        avtech, manager = open_generator(args.resource, args.dry_run)
    except Exception as error:
        print(f"Could not open {args.resource}: {error}. Nothing was sent.")
        return 1
    verified = False
    try:
        try:
            avtech.identity = avtech.query("*IDN?")
        except Exception as error:
            print(f"{args.resource} did not answer *IDN?: {error}. Nothing else was sent.")
            return 1
        if "AVTECH" not in avtech.identity.upper():
            print(f"{args.resource} is not an Avtech generator: {avtech.identity!r}. Nothing else was sent.")
            return 1
        verified = True
        allowed = avtech.polarities
        print("Polarity: " + ("not in the model name; each pulse's sign is checked" if allowed is None else
                              {(1,): "positive only (-P)", (-1,): "negative only (-N)",
                               (1, -1): "both (-PN)"}[allowed]))
        if args.mode == "info":
            read_only(avtech)
            return 0
        if args.mode == "off":
            problem = avtech.off_problem()
            print("Trigger on hold, output off (read back)." if problem is None
                  else f"Output state unknown: {problem}. CHECK THE GENERATOR.")
            return 0 if problem is None else 3
        # pulse: first the trigger on hold and the output off, read back
        problem = avtech.off_problem()
        if problem is not None:
            print(f"Output state unknown: {problem}. CHECK THE GENERATOR.")
            return 3
        print("Trigger on hold, output off (read back).")
        print(f"\nOne pulse: {args.amplitude:+g} V, {args.width:g} ns, on {args.resource}.")
        print("Is OUT terminated in 50 ohm (a scope or the sample with its 50 ohm termination)?")
        if not args.yes and input('Type "yes" to fire: ').strip().lower() != "yes":
            print("Not fired.")
            return 1
        try:
            amplitude, width, overload = avtech.single_pulse(args.amplitude, width_s)
        except PulseError as error:
            print(f"\n{error}")
            return 3 if getattr(error, "off_unknown", False) else 1
        print(f"\nPulse fired: {amplitude:+g} V, {width * 1e9:g} ns (read back); output off (read back)."
              + ("\nOVERLOAD: output protection tripped (short or too-low load impedance)." if overload else ""))
        if args.dry_run:
            print(f"(simulated pulses: {avtech.instrument.pulses})")
        return 0
    except KeyboardInterrupt:
        if args.mode == "info":
            print("\nStopped; nothing was set (read only).")
            return 1
        if not verified:
            print("\nStopped before the identity was confirmed; nothing else was sent.")
            return 1
        try:
            problem = avtech.off_problem()
        except BaseException as error:
            problem = f"the off sequence was interrupted ({error!r})"
        print("\nStopped; output off (read back)." if problem is None
              else f"\nStopped; output state unknown: {problem}. CHECK THE GENERATOR.")
        return 3 if problem is not None else 1
    finally:
        try:
            avtech.instrument.close()
        except Exception:
            pass
        if manager is not None:
            try:
                manager.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
