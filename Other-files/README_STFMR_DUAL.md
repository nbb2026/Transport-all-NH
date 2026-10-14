# STFMR dual lock-in measurement

Main program: `Magneto_Electro_Optical_Microwave_Transport_NH_dev.py`.

Select `MFLI + SR830` under "Meter" on the STFMR page and connect both lock-ins plus the
KEPCO 4886, LS475 and SMB100A. The single-meter `MFLI` and `SR830` modes remain.
The meter mode is locked during a scan; connecting an instrument in dual mode does not switch back to single mode.
Settle s, Acquire s, "SR830 TC" and "MFLI TC s" are read at Start and locked during a scan too (2026-10-14),
and so are "P start dBm", "P stop dBm" and "P step dBm" (user, 2026-10-14).

## Acquisition and display

- Settle s and Acquire s replace Wait/pt (2026-10-14). After each current step a point settles
  max(Settle s, 7 × the TC of every lock-in read: the "SR830 TC" choice when the SR830 is read, "MFLI TC s" when
  the MFLI is), 7 s at the defaults (Settle s 1, both TCs 1 s; 12 dB/oct, 99 % settled). Then the field is read once
  and the MFLI and SR830 in turn every 0.1 s, at the same current, until Acquire s (default 2 s) has passed since the
  point's first lock-in reading started; each meter's row holds the mean X and Y of its readings over that same
  window, with R and θ from the mean X and Y, and `sample_count` readings (21 at the defaults).
  This is not hardware-synchronous triggering; the two meters differ by communication time, and each row stores the
  read-completion time of its meter's last reading.
- Each instrument runs its own reference configuration and autorange; only a point's first reading may step a range
  down, the later readings of its window only up (overload or near full scale). If either range changes, the point's
  readings of both meters are discarded and the same point is read again, from its first reading, after the wait:
  max(0.1 s, the settle, 10 × the MFLI TC) after an SR830 change, max(the settle, 10 × the MFLI TC, 0.2 s) after an
  MFLI change, so 10 s at the defaults. In dual mode, any read failure, reference loss, invalid SR830 status, MFLI
  overload or invalid X/Y/Theta (missing or non-finite) in any reading of the window aborts the scan through the
  existing output-off sequence, and the point is not saved; so does any other failure in checking a reading.
- The SR830 time constant is the "SR830 TC" choice (1 ms to 10 s, default 1 s since 2026-10-14, 100 ms before;
  12 dB/oct). Until 2026-10-14 an SR830 point waited the longer of Wait/pt and 5 × its time constant; the MFLI
  alone waited Wait/pt.
- Discarded for both meters: the start pre-read, the first two points of each frequency (the 4886 has just gone
  from 0 to Start A, with a new carrier, power or modulation frequency) and the first two points of each later loop, which starts with the jump
  from Stop A back to Start A. A discarded point is one reading, with no Acquire s window; the first of each
  frequency follows the start pre-read at once, the second settles after its step as every later point does. A fixed current (Start A = Stop A) has no such jump: Loop is then the number of
  readings at each frequency, and all but the first two are saved, so it needs a Loop of at least 3. Start refuses
  a run that would save nothing (two currents per loop, or a fixed current with Loop 1 or 2) (2026-10-13).
- Modulation frequencies (2026-10-13): "Mod. start Hz" = "Mod. end Hz" (117.3 by default since 2026-10-14; 317.3 before) is the one LFO
  frequency, as before; otherwise every whole-number prime between them, from start towards end, with no step.
  The whole f × A sweep runs at each in turn, modulation outermost. At each change the 4886 goes to zero, the
  SMB100A LFO frequency changes (RF, AM and LFO stay on), the carrier returns to f start and, as at the start,
  the SR830 must lock to the new frequency within 5 s, its readback nearer the new frequency than the old one
  (neighbouring primes less than 0.5 % apart both pass the 0.5 % match), before the 4886 comes back on; both meters then expect the
  new frequency, and the start pre-read and the first two points are discarded as after a carrier change.
- Microwave powers (user, 2026-10-14): "P start dBm", "P stop dBm" and "P step dBm" replace "Power dBm"; 12, 12
  and 1 by default, so one power of 12 dBm (6 dBm before). Equal start and stop give that one power; otherwise the
  list runs from start towards stop in the step and ends on stop, as the f list does; every power must be finite and
  at most 25 dBm, and at most 1000 powers, or Start refuses before any output or file. The order is modulation
  (outermost), power, carrier, current: the whole f × A sweep runs at each power. At each power change the 4886 goes
  to zero, the SMB100A gets only `:POW <value>` and its level is read back (`:POW?`, more than 0.01 dB off stops the
  scan with the outputs off); RF, AM and LFO stay on, like the 6221 in a current scan. The carrier returns to f
  start, the 4886 comes back on at Start A, and the start pre-read and the first two points are discarded as after
  a carrier change; there is no reference wait, as the LFO does not change. At a modulation change with several
  powers the first power is set again before the reference wait. The SMB100A is switched off only at the end, on
  Stop or on an error.
- X, Y and Theta show both curves in one plot: MFLI as filled circles, SR830 as thin-edged hollow circles; the legend names instrument and frequency, and the modulation frequency and the power when a sweep has several (powers: 2026-10-14).
  Hollow circles are slightly larger for legibility, with a 0.75 px edge in the GUI; the legend uses the same marker style.
  Voltage, phase and reference readings are shown per meter. Idle readings are not appended to the scan curves.
- The bottom-right plot is still setpoint current vs measured field, drawn once without repeated overlay.
- Each instrument must have its own measurement input and reference wiring; the software does not route hardware signals.
  Before parallel measurement, check input impedance, grounding and allowed voltage; never connect instrument outputs directly together.

## Files

The live CSV keeps its column names; in dual mode each retained field point writes two rows with `meter`
`MFLI` and `SR830`, sharing `index`, frequency, loop, current and field.
**The unique record key is index + meter; do not de-duplicate on index alone.**
Each row's X/Y/R/Theta and range parameters belong only to that instrument; the column
`sr830_time_constant_s` (added 2026-10-13) is filled on SR830 rows and `nan` on MFLI rows. The last four columns
(2026-10-14) are `settle_s`, `effective_settle_s` (the settle the point waited), `acquire_s` and `sample_count`
(that meter's readings in the row's mean); every older column keeps its place.
`modulation_frequency_hz` is the LFO frequency the row was read at (one per modulation pass; the column is not new).
`microwave_power_dbm` is the SMB100A power the row was read at (one per power pass; the column is not new; 2026-10-14).
`timestamp_iso` is each instrument's last read-completion time in the window, not a hardware-synchronous sample time.
The log's `expected_saved_points` / `saved_points` count CSV rows, twice the number of field points in dual mode;
GUI progress still advances per field point. The field and the two meters are not sampled at the same instant; allow for settling time.

Row-by-row writing is unchanged: a disk failure on the second row may leave the first row behind, and the closing log
reports an error; when handling abnormal or interrupted files, check for paired records and do not treat an unpaired row as a complete dual point.
The end of a sweep writes no PNG; the live CSV and log are the record, and the original single-meter files remain compatible.
PNGs are written by "Save CSV" (user, 2026-10-13): it writes `STFMR_field_scan_<time>.csv` first, then four 900 DPI
figures next to it, `..._X.png`, `..._Y.png`, `..._theta.png` and `..._field_vs_current.png`, drawn from the saved rows
by `_export_stfmr_lockin_data_pngs` (both curves with their marker shapes in dual mode; the plots need not be on screen).
The status line shows "Saving PNG…" while they are drawn (about a second; the page, Stop included, waits meanwhile). If they fail,
the CSV is still saved: the status line and a warning name the PNG problem, and a figure written before it stays.
Existing PNG files are never replaced.

## Verification

2026-09-07: Python 3.9.6 passed the dual and original live-recording regressions, 65 instrument safety-logic tests,
13 MFLI range and 5 overload regressions. The dual tests cover configuration, paired CSV, range re-read,
read failure, reference loss, stop with output off, connection pre-checks and the GUI filled / thin hollow circle markers.
Four 900 DPI PNGs were exported from simulated data and inspected.
No real instruments were connected; Windows, actual wiring and dual-meter signal consistency still need experimental verification.

Run: `PYTHONPATH=. python test_stfmr_dual.py`.
No EXE was built; the standalone frequency-sweep program and the low-level instrument protocols were not modified.
