# MS-fourState

Writes the magnetic states D, A, B and E in the PPMS with a field and a 6221
current pulse at a rotator angle, then reads each state at zero field with the
SR830 first harmonic. The entry point is `MS-fourState.py` in the repository
root. It uses the instrument code of `Transport_all_NH_dev.py`, which must sit
in the same folder.

```console
python MS-fourState.py
```

Close the PPMS, 6221 and SR830 connections in `Transport_all_NH_dev.py` first.
Two programs must never drive the same instruments.

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

The sequence is typed as state names, for example `D A B E` (the default),
`A B A B D E` or `D A' B' E`, and can be repeated.

## One step

1. 6221 off. If the field is not at zero, it is set to 0 Oe (Driven).
2. The rotator turns to the state's angle, only at zero field and with the
   6221 off. Turns over 30° get the 900 s arrival limit of the PPMS page's
   manual Move; the start dialog counts them.
3. The write field is set, Driven, at the field rate. The pulse waits until
   the PPMS reports a Driven settled status at the field (within 2 Oe) for
   the settle time.
4. The write pulse: 0 → I for the pulse width (default 30 µs, at least 5 µs),
   one pulse by default. It uses the 6221 code of the MOKE-6221 page: a 50 %
   square of period 2 × width, a finite cycle count, every setting read back.
   The output must be off once the train is over, or the run stops.
5. The field returns to 0 Oe (Driven) and settles.
6. Read: the 6221 sources a sine of the read current (default 0.5 mA RMS at
   317.3 Hz). Its phase marker is the SR830's external reference, as on the
   PPMS page. Once the SR830 locks, the program waits 5 time constants plus
   the extra settle time and, by default, autoranges: one range up on
   overload, one down below 10 % of full scale. X, Y and the frequency are
   then sampled for the read time and averaged. θ is that of the mean
   vector, and R'xy = X / I_read (RMS).
7. 6221 off. The point is plotted and written to the CSV.

The SR830 is set once at the start to the first harmonic and the external
reference, with the line and sync filters off: `ILIN 0`, `SYNC 0`, `HARM 1`,
`FMOD 0`, `RSLP 1`, each checked with `*ESR?` and read back. The time
constant and input settings are left as they are.

## Stops

Stop, an error, or the end of the run turns the 6221 off and, if the field is
not at zero, sets 0 Oe (Driven). The rotator is never moved by a stop. It has
no software stop, so a Stop during a rotation does not stop the motor: keep
MultiVu's Motion Control at hand. The run also stops on:

- 6221 compliance during the read
- the SR830 not locking to the 6221 reference within 8 s, or losing it
  during the read
- an SR830 overload that autoranging cannot clear
- a field that does not settle within the ramp time plus 10 min

## Data

Each run gets a folder `MS-fourState data/<date_time>/` next to the program:

- `MS-fourState.csv`: one row per step, written as it is measured: state,
  angle set and read, write field set and read, pulse current, width, count
  and compliance, read field, temperature, read current and frequency, X, Y,
  R, θ, the standard deviations of X and Y, the SR830 range and R'xy.
- `settings.json`: every setting of the run and the instruments' identities.
- `log.txt`: the status and instrument log.
- `MS-fourState_plot.eps`: the plot, saved at the end and by **Save plot**.

The plot shows, per step, the write field in T, the pulse current in mA, and
SR830 X in µV, with each point coloured by its state.

## Defaults to check before the first run

- 6221 compliance 15 V, for both the pulse and the read. At 22 mA this allows
  up to about 680 Ω. Raise it if the sample needs more; the read stops on
  compliance, but a pulse this short is not monitored for it.
- Field rate 100 Oe/s, "No overshoot", 2 s settle, 1 s wait after the pulse.
- Rotator decel 14 (slowest).
- Read 0.5 mA RMS, 317.3 Hz, 3 s, 1 s extra settle, autorange on.

## Tests

`Other-files/test_ms_four_state.py` runs the program's measurement code
against simulated instruments: a PPMS that ramps the field in simulated time,
a rotator, a 6221 and an SR830 simulated at the level of their commands, and
a sample that takes the state written by the last pulse. They check the order
of every action, the values read, and each stop. A cloud check,
`Other-files/ci/ms_four_state_checks.py`, builds the window on GitHub's Linux
and Windows machines and saves screenshots. Nothing has been run on the
instruments yet.
