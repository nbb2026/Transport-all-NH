# MS-fourState, MS-cycleState, MS-twoState

Write the magnetic states D, A, B and E in the PPMS with a field and a 6221
current pulse at a rotator angle, then read each state at zero field with the
SR830 first harmonic. Three entry points in the repository root open the same
window in three ways:

| Program | Runs |
| --- | --- |
| `MS-fourState.py` | A typed sequence, default `D A B E`, a set number of times or until Stop |
| `MS-cycleState.py` | A cycle, default `D A B E B A`, so D-A-B-E-B-A-D-A-B-…, until Stop (default) or for a set number of cycles |
| `MS-twoState.py` | Switching between any two states chosen from two lists, default A ⇄ B, until Stop (default) or for a set number of pairs |

```console
python MS-fourState.py
python MS-cycleState.py
python MS-twoState.py
```

All three use the instrument code of `Transport_all_NH_dev.py`, and the two
launchers use `MS-fourState.py`, so the four files must sit in the same
folder. Close the PPMS, 6221 and SR830 connections in `Transport_all_NH_dev.py`
first. Two programs must never drive the same instruments.

## States

Defaults from the measurement table, editable in the window (1500 Oe = 0.15 T):

| State | φ PPMS | H write | I pulse | |
| --- | --- | --- | --- | --- |
| D | 0° | +1500 Oe (Hz) | +22 mA | +H ⊥ +I |
| A | 270° | −1500 Oe (Hx) | +22 mA | +H ∥ +I |
| A' | 270° | +1500 Oe (Hx) | −22 mA | A, written the other way |
| B | 270° | +1500 Oe (Hx) | +22 mA | +H ∥ +I |
| B' | 270° | −1500 Oe (Hx) | −22 mA | B, written the other way |
| E | 0° | −1500 Oe (Hz) | +22 mA | −H ⊥ +I |

Sequences and cycles are typed as state names separated by spaces, commas or
dashes, for example `D A B E`, `D-A-B-E-B-A` or `D A' B' E`. A cycle repeats
from its first state. Switching between A and B, or between D and E, needs no
rotation; switching between, for example, A and D turns the rotator 270° each
time.

## One step

1. 6221 off, confirmed by `OUTP? = 0`. If the field is not at zero, it is set
   to 0 Oe (Driven).
2. The rotator turns to the state's angle, only at zero field and with the
   6221 off. Turns over 30° get the 900 s arrival limit of the PPMS page's
   manual Move; the start dialog counts them per pass.
3. The write field is set, Driven, at the field rate. The pulse waits until
   the PPMS reports a Driven settled status at the field (within 2 Oe) for
   the settle time.
4. The write pulse: 0 → I for the pulse width (default 30 µs, at least 5 µs),
   one pulse by default. The commands are those of the MOKE-6221 page: a
   50 % square of period 2 × width with offset I/2 and a finite cycle count,
   then ARM, `OUTP ON` and INIT in that order, each checked. Every setting
   is read back before ARM. The 6221's latched compliance bit
   (`STAT:MEAS:EVEN?`) is read around the train. The CSV says `True` or
   `False`, or `unknown` if the 6221 does not answer it; then it is not
   asked again.

   The output must be off once the train is over, or the run stops.
5. The field returns to 0 Oe (Driven) and settles.
6. Read: the 6221 sources a sine of the read current (default 0.5 mA RMS at
   317.3 Hz, at most 5 mA). Its phase marker is the SR830's external
   reference, as on the PPMS page; amplitude, frequency, zero offset and
   compliance are read back. Once the SR830 locks, the program waits 5 time
   constants plus the extra settle time and, by default, autoranges. That
   is one range up on overload, one down below 10 % of full scale, and
   never down again after going up. X, Y and the frequency are then sampled
   for the read time and averaged, with the 6221 output and compliance
   checked at every sample. θ is that of the mean vector, and
   R'xy = X / I_read (RMS).
7. 6221 off. The point is plotted and written to the CSV.

## The SR830

On **Connect all** the SR830 gets the PPMS page's baseline, each setting
checked with `*ESR?` and read back. The baseline is: first harmonic,
external reference (`FMOD 0`, `RSLP 1`), A−B input, shield floating,
AC-coupled, line and sync filters off, 1 s time constant, 12 dB/oct, normal
reserve, phase 0, starting range 2 µV.

At each Start, `*CLS` clears any error latched by an earlier disconnect. The
first harmonic, the external reference and the filters are then set again
and read back. Everything else is recorded as found in `settings.json`. A
time constant above 10 s is refused.

## Stops

Stop, an error, or the end of the run turns the 6221 off and returns the
field to 0 Oe (Driven), both confirmed by readback. Shutdown follows the field
this run commanded, not a reading that may still be stale. It waits for zero
up to the ramp time plus 2 min. If either is not confirmed, the status line
and a dialog say so.

The rotator is never moved by a stop. It has no software stop, so a Stop
during a rotation does not stop the motor: keep MultiVu's Motion Control at
hand.

The run also stops on:

- 6221 compliance during the read
- the SR830 not locking to the 6221 reference within 8 s, or losing it
  during the read
- an SR830 overload that autoranging cannot clear
- a field that does not settle within the ramp time plus 10 min

## Data

Each run gets a folder `<program> data/<date_time>/` next to the program,
for example `MS-cycleState data/20261006_153000/`:

- `<program>.csv`: one row per step, written as it is measured. The columns
  are: state and cycle; angle set and read; write field set and read; pulse
  current, width, count, compliance and compliance seen; read field;
  temperature; read current and frequency; X, Y, R, θ and the standard
  deviations of X and Y; the SR830 range; and R'xy.
- `settings.json`: every setting of the run, the mode, the SR830 settings
  found at Start, the SR830 output offsets and
  expands, and the instruments' identities.
- `log.txt`: the status and instrument log of that run.
- `<program>_plot.eps`: the plot, saved at the end and by **Save plot**.

The plot shows, per step, the write field in T, the pulse current in mA, and
SR830 X in µV, with each point coloured by its state. State letters are drawn
for up to 60 points.

## Before the first run

- **Pulse shape:** check the pulse once with an oscilloscope across a
  dummy resistor in place of the sample. Nobody has measured what the 6221
  outputs between ARM, `OUTP ON` and INIT. If it sits at the I/2 offset for
  those few milliseconds, the sample sees 11 mA DC before each 30 µs pulse.
  The MOKE-6221 page uses the same order. If the scope shows that, the pulse
  start needs changing before measuring.
- **Compliance:** the 6221 compliance is 15 V, for both the pulse and the
  read. At 22 mA this allows up to about 680 Ω in the current path. Raise
  it if the sample needs more.
- **Other defaults:** field rate 100 Oe/s, "No overshoot", 2 s settle, 1 s
  wait after the pulse; rotator decel 14 (slowest); read 0.5 mA RMS,
  317.3 Hz, 3 s, 1 s extra settle, autorange on.
- **End state:** a run ends Driven at 0 Oe. Use the PPMS page's Zero H or
  Persist if the magnet should rest Persistent.

## Tests

`Other-files/test_ms_four_state.py` runs the measurement code of all three
modes against simulated instruments:

- a PPMS that ramps the field in simulated time and can report a stale
  reading after a command;
- a rotator;
- a 6221 and an SR830 simulated at the level of their commands, with
  latched errors and compliance;
- a sample that takes the state written by the last pulse.

They check the order of every action, the values read, each stop and the
shutdown confirmation. Each safety rule was broken in a copy, and the tests
catch all of them. A cloud check, `Other-files/ci/ms_four_state_checks.py`,
builds the three windows on GitHub's Linux and Windows machines and saves
screenshots. Nothing has been run on the instruments yet.
