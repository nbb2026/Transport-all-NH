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
- The start pre-read and the first two points of each frequency are discarded for both meters.
- X, Y and Theta show both curves in one plot: MFLI as filled circles, SR830 as thin-edged hollow circles; the legend names instrument and frequency.
  Hollow circles are slightly larger for legibility, with a 0.75 px edge in the GUI; legend and PNG use the same marker style.
  Voltage, phase and reference readings are shown per meter. Idle readings are not appended to the scan curves.
- The bottom-right plot is still setpoint current vs measured field, drawn once without repeated overlay.
- Each instrument must have its own measurement input and reference wiring; the software does not route hardware signals.
  Before parallel measurement, check input impedance, grounding and allowed voltage; never connect instrument outputs directly together.

## Files

The live CSV keeps its column names; in dual mode each retained field point writes two rows with `meter`
`MFLI` and `SR830`, sharing `index`, frequency, loop, current and field.
**The unique record key is index + meter; do not de-duplicate on index alone.**
Each row's X/Y/R/Theta and range parameters belong only to that instrument.
`timestamp_iso` is each instrument's read-completion time, not a hardware-synchronous sample time.
The log's `expected_saved_points` / `saved_points` count CSV rows, twice the number of field points in dual mode;
GUI progress still advances per field point. The field and the two meters are not sampled at the same instant; allow for settling time.

Row-by-row writing is unchanged: a disk failure on the second row may leave the first row behind, and the closing log
reports an error; when handling abnormal or interrupted files, check for paired records and do not treat an unpaired row as a complete dual point.
No PNG is written: neither the end of a sweep nor "Save CSV" (which writes the CSV only) exports one; the live CSV and log
are the record, and the original single-meter files remain compatible. The program's 900 DPI exporter
(`_export_stfmr_lockin_data_pngs`, both curves with their marker shapes) is not connected to the page
(audit, 2026-10-13: this line used to say that PNGs were exported automatically and manually).

## Verification

2026-09-07: Python 3.9.6 passed the dual and original live-recording regressions, 65 instrument safety-logic tests,
13 MFLI range and 5 overload regressions. The dual tests cover configuration, paired CSV, range re-read,
read failure, reference loss, stop with output off, connection pre-checks and the GUI filled / thin hollow circle markers.
Four 900 DPI PNGs were exported from simulated data and inspected.
No real instruments were connected; Windows, actual wiring and dual-meter signal consistency still need experimental verification.

Run: `PYTHONPATH=. python test_stfmr_dual.py`.
No EXE was built; the standalone frequency-sweep program and the low-level instrument protocols were not modified.
