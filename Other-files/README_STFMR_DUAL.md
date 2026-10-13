# STFMR dual lock-in measurement

Main program: `Magneto_Electro_Optical_Microwave_Transport_NH_dev.py`.

Select `MFLI + SR830` under "Meter" on the STFMR page and connect both lock-ins plus the
KEPCO 4886, LS475 and SMB100A. The single-meter `MFLI` and `SR830` modes remain.
The meter mode is locked during a scan; connecting an instrument in dual mode does not switch back to single mode.

## Acquisition and display

- At each current setpoint the field is read once, then the MFLI and SR830 in turn; the current is not changed meanwhile.
  This is not hardware-synchronous triggering; the two meters differ by communication time, and each row stores its read-completion time.
- Each instrument runs its own reference configuration and autorange. If either range changes, both current
  readings are discarded and the same point is re-read after the wait. In dual mode, any read failure, reference loss,
  invalid SR830 status, MFLI overload or invalid X/Y/Theta aborts the scan through the existing output-off sequence.
- The SR830 time constant is the "SR830 TC" choice (1 ms to 10 s, default 100 ms; 12 dB/oct). Whenever the
  SR830 is read, in dual mode too, each point and each re-read after a range change waits the longer of Wait/pt and
  5 × the time constant (0.5 s with the default 100 ms, the same as the default Wait/pt; 5 s at 1 s), except the discarded first point of
  each frequency, read right after the start pre-read as before (2026-10-13).
- Discarded for both meters: the start pre-read, the first two points of each frequency (the 4886 has just gone
  from 0 to Start A, with a new carrier) and the first two points of each later loop, which starts with the jump
  from Stop A back to Start A. A fixed current (Start A = Stop A) has no such jump: Loop is then the number of
  readings at each frequency, and all but the first two are saved, so it needs a Loop of at least 3. Start refuses
  a run that would save nothing (two currents per loop, or a fixed current with Loop 1 or 2) (2026-10-13).
- Modulation frequencies (2026-10-13): "Mod. start Hz" = "Mod. end Hz" (317.3 by default) is the one LFO
  frequency, as before; otherwise every whole-number prime between them, from start towards end, with no step.
  The whole f × A sweep runs at each in turn, modulation outermost. At each change the 4886 goes to zero, the
  SMB100A LFO frequency changes (RF, AM and LFO stay on), the carrier returns to f start and, as at the start,
  the SR830 must lock to the new frequency within 5 s before the 4886 comes back on; both meters then expect the
  new frequency, and the start pre-read and the first two points are discarded as after a carrier change.
- X, Y and Theta show both curves in one plot: MFLI as filled circles, SR830 as thin-edged hollow circles; the legend names instrument and frequency, and the modulation frequency when a sweep has several.
  Hollow circles are slightly larger for legibility, with a 0.75 px edge in the GUI; the legend uses the same marker style.
  Voltage, phase and reference readings are shown per meter. Idle readings are not appended to the scan curves.
- The bottom-right plot is still setpoint current vs measured field, drawn once without repeated overlay.
- Each instrument must have its own measurement input and reference wiring; the software does not route hardware signals.
  Before parallel measurement, check input impedance, grounding and allowed voltage; never connect instrument outputs directly together.

## Files

The live CSV keeps its column names; in dual mode each retained field point writes two rows with `meter`
`MFLI` and `SR830`, sharing `index`, frequency, loop, current and field.
**The unique record key is index + meter; do not de-duplicate on index alone.**
Each row's X/Y/R/Theta and range parameters belong only to that instrument; the last column,
`sr830_time_constant_s` (added 2026-10-13), is filled on SR830 rows and `nan` on MFLI rows.
`modulation_frequency_hz` is the LFO frequency the row was read at (one per modulation pass; the column is not new).
`timestamp_iso` is each instrument's read-completion time, not a hardware-synchronous sample time.
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
