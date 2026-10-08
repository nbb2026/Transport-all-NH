# MS-fourState

Write the magnetic states D, A, B and E in the PPMS with a field and a 6221
current pulse at a rotator angle, then read each state at zero field with the
SR830 first harmonic. One program in the repository root:

```console
python MS-fourState.py
```

The **Mode** list in the Run row chooses how it runs:

| Mode | Runs |
| --- | --- |
| Cycle (default) | A cycle of states, default `D A B E B A`, repeated from its first state (D-A-B-E-B-A-D-A-B-…) for the number of **Cycles** set, or until Stop if that box is ticked |
| Sequence | The states once, in the order typed, for example `D A B E B A D`, or `A B A B A B` to switch between two states |

Each mode keeps its own states, so switching between them loses nothing.

It uses the instrument code of `Transport_all_NH_dev.py`, which must sit in
the same folder. Close the PPMS, 6221 and SR830 connections in `Transport_all_NH_dev.py`
first. Two programs must never drive the same instruments.

## Connections

Each instrument has its own row: name, address, **Connect**, **Disconnect**,
a lamp and its state, as on the main program's pages. The lamp is grey when
not connected, green when connected and red after a failed connect. The
reason is shown beside it. Connecting or disconnecting one instrument leaves
the others as they are. The 6221 turns its output off when it disconnects;
disconnecting the PPMS leaves the field and angle. An address is fixed while
its instrument is connected.

The window opens at 1720 × 1240 where the screen allows (the lab PC's
2560 × 1440 does), or smaller on a smaller screen, so the plot has the room.

**PPMS status**, to the right of Connections (under it in a narrow window),
shows what the PPMS page's live readings show:
- temperature, field and angle in large type, each with its status;
- chamber pressure and helium level (the PPMS page's own read, which never
  starts a fill);
- the 6221 output, and the time of the reading.

It is read only:
- **Idle:** read every 2 s; pressure and helium every 30 s.
- **During a run or a manual action:** it shows that action's own readings,
  at most one a second, and each rotation. Nothing extra is asked of the
  instruments then.

The setup boxes sit side by side at their own widths: Connections, PPMS
status, Manual settings, States, Write and Read. A box moves to a new line
only when the window is too narrow, and the boxes on a line share its height
and spare width. On the lab PC's 1720 px window they make two lines. The
Write box has two columns: the pulse on the left (width, count, compliance,
wait), the field and rotator on the right.

## Console

Right of the plot, under **Last read** (the latest state's X, Y, θ, R'xy, field
and temperature), is a console in the style of a command-line session.
- **Program messages:** run steps, warnings and connections.
- **Instrument commands:** with the **Instrument commands** tick, every
  command sent to an instrument and every reply, in order:
  - `6221 > SOUR:WAVE:AMPL 1.1e-02`: a command sent;
  - `SR830 < 1.1e-05,…`: a reply;
  - `SR830 ! VI_ERROR_TMO`: a failed exchange.
- **What is covered:** the 6221 and SR830 (every write and query, from
  `*IDN?` at Connect on), the PPMS (SetField, readings, connect), and the
  rotator (MOVE, GetPPMSItem).
- **Repeats:** a line repeated unchanged, such as a poll that reads the same,
  is counted on its line (×3) instead of repeated.
- **Colours:** sent in blue, replies in grey, failures in red.
- **Keeping it:** **Clear** empties the console. During a run every line also
  goes to the run's `log.txt`. The console keeps its last 3000 lines.

## Manual settings

Between runs, the **Manual settings** box sets one thing at a time, as the
PPMS settings rows of `Transport_all_NH_dev.py` do:

| Row | Fields | Buttons |
| --- | --- | --- |
| Angle | Angle °, Decel 0–14 | **Move**, **Read** |
| Field | H Oe, Oe/s | **Set H**, **Zero H** |
| Current | I mA RMS, f Hz | **Set I**, **I off** |
| Pulse | Pulse mA, Width µs | **Pulse** |

- **Move:** turns the rotator as a run step does. It is refused, with
  nothing sent or turned off, unless the field is at 0 Oe (Zero H first) and
  the rotator is at rest. A turn over 30° asks for confirmation, as on the
  PPMS page, and the field is read again after the answer. Then the 6221 is
  turned off and the rotator moves.
- **Set H and Zero H:** go to the field and end **Persistent**, as on the
  PPMS page.
  - Done when the readback shows Persistent at the field, by that page's
    rule, so a reading from before the command cannot pass. Nothing is resent.
  - The Now line shows the field every 2 s while it waits.
  - MagnetFailure or CurrentError ends it at once.
- **Set I:** starts the read sine (I mA RMS at f Hz, phase marker on, as in a
  read) and leaves it on. The SR830's lock to it is shown; nothing is written
  to the SR830. **I off** turns the 6221 off, read back.
- **Pulse:** sends one write of Pulse mA, Width µs wide, after a
  confirmation, then turns the 6221 off. The pulse count and compliance come
  from the Write box. The width is the manual Pulse's own (it starts at
  30 µs) and does not change the runs' pulse width.
- **Read:** shows the angle, field, temperature and 6221 output. Each action
  also updates this readout when it ends.

Decel, Oe/s and f Hz are the Write and Read boxes' own fields: changing one
changes both. They are checked for manual actions as for runs: the field
rate below 150 Oe/s, and the compliance within the 6221's range. Every
button except I off and Read needs the safety tick in the Run row.

As on the PPMS page, nothing is sent while the rotator may be moving:
- **Rotator not at rest:** Set H, Set I, Pulse and Move are refused unless it
  reads AtTarget.
- **Move stopped or failed:** those buttons and Start stay refused until a
  Read shows the rotator at rest.
- **Set H failed:** Move is refused until a Zero H succeeds, as the PPMS may
  still be going to that field.

Every manual action and its result is written to `manual_log.txt` in the
data folder.

**Stop** during a manual action does what it does in a run: 6221 off and
0 Oe (Driven), read back. That includes a field set earlier with Set H.
- **Stop just as the action ends:** still honoured.
- **Instrument not connected:** left out of the check, not reported as a
  problem.

An error turns the 6221 off and leaves the angle. After a failed Set H, the
PPMS may still be going to that field; the command is not resent, so use
Zero H or MultiVu.

## States

Defaults from the measurement table, editable in the window (1500 Oe = 0.15 T):

| State | φ PPMS | H write | I pulse | |
| --- | --- | --- | --- | --- |
| D | 360° | +1500 Oe (Hz) | +22 mA | +H ⊥ +I |
| A | 270° | +1500 Oe (Hx) | −22 mA | +H ∥ −I |
| B | 270° | +1500 Oe (Hx) | +22 mA | +H ∥ +I |
| E | 360° | +1500 Oe (Hz) | −22 mA | +H ⊥ −I |

Cycles and sequences are typed as state names separated by spaces, commas or
dashes, for example `D A B E` or `D-A-B-E-B-A`. Switching between
A and B, or between D and E, needs no rotation. Switching between, for
example, A and D turns the rotator 90° each time. D and E are at 360°, the
same orientation as 0°, so that this turn is 90° rather than 270° (changed
2026-10-08). If the rotator reads 0° when a run starts, its first move to
360° is one full turn. It is confirmed in the summary as a large rotation.

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

On **Connect** the SR830 gets the PPMS page's baseline, each setting
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

Each run gets a folder `MS-fourState data/<date_time>_<mode>/` next to the
program, for example `MS-fourState data/20261007_153000_cycle/`:

- `MS-fourState.csv`: one row per step, written as it is measured. The columns
  are: state and cycle; angle set and read; write field set and read; pulse
  current, width, count, compliance and compliance seen; read field;
  temperature; read current and frequency; X, Y, R, θ and the standard
  deviations of X and Y; the SR830 range; and R'xy.
- `settings.json`: every setting of the run, the mode, the SR830 settings
  found at Start, the SR830 output offsets and
  expands, and the instruments' identities.
- `log.txt`: the status and instrument log of that run.
- `MS-fourState_plot.eps`: the plot, saved at the end and by **Save plot**.

The plot is a timing diagram of the run against the time since it started,
in s (min after 10 min, h after 3 h). Each period shows at its real time and
for its real length:
- **H (T):** the field as the PPMS reads it, ramping to the write field and
  back to 0 Oe for the read.
- **I (mA):** the 6221. Each write pulse is a spike in its state's colour (it
  is microseconds long), and the read sine is a block at its RMS level while
  it is on.
- **SR830 X (µV):** every sample of each read, in its state's colour, with
  the state's letter above it.
- **Read periods:** each period with the read current on is shaded in all
  three panels. 0.5 mA hardly shows beside the pulses, but the shading marks
  when the readback was taken.

The same timeline is saved as `MS-fourState_trace.csv` in the run folder,
with columns time_s, quantity, value, unit and state. The quantities are H
(Oe), I (mA RMS), pulse (mA), X (V) and read (V, the read's mean). A state
read again after a time therefore shows how long it is kept.

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

`Other-files/test_ms_four_state.py` runs the measurement code in all three
modes against simulated instruments:

- a PPMS that ramps the field in simulated time and can report a stale
  reading after a command;
- a rotator;
- a 6221 and an SR830 simulated at the level of their commands, with
  latched errors and compliance;
- a sample that takes the state written by the last pulse.

They check the order of every action, the values read, each stop and the
shutdown confirmation. The manual settings are tested the same way:
- Move only at zero field with the 6221 off, and large turns confirmed;
- Set H ending Persistent, with a stale reading not taken for arrival;
- Stop returning to 0 Oe, and an error leaving the field;
- Set I writing nothing to the SR830;
- the pulse using the Write settings. Each safety rule was broken in a copy, and the tests
catch all of them. A cloud check, `Other-files/ci/ms_four_state_checks.py`,
builds the window in each mode on GitHub's Linux and Windows machines and saves
screenshots. Nothing has been run on the instruments yet.
