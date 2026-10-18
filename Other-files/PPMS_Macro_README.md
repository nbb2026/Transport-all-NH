# PPMS Macro (development build)

Entry: the Macro button in the PPMS external-instruments area. PPMS1 / PPMS2 run independently.
The window supports editing, loading/saving .seq, whole-script validation, line-by-line highlighted execution, stop, and immediate 6221/2400 output off.

Supported commands:

- `REM note` or `# note`: comment.
- `TEMP 300`: set temperature; without a rate, the run picks it from the live temperature: 10 K/min only when both the live temperature and the target are above 9 K, otherwise 1 K/min (the log names the rate; the status line shows `auto`).
- `TMP TEMP 300 10 0`: temperature K, rate K/min, approach (0/1). The existing temperature safety validation applies; a start below 10 K may require a lower rate.
- `FIELD 0 100 0 0` / `FLD FIELD 0 100 0 0`: field Oe, rate Oe/s, approach (0/1/2), mode (0 Persistent, 1 Driven). With two parameters the defaults are approach=0, mode=1.
- `WAIT 10`: delay 10 seconds.
- `WAI WAITFOR 10 1 1 0 0 0`: wait for the previously set temperature and field to stabilise, then hold for 10 s; the temperature/field flags can be set to 0 to skip. With field mode=0, Persistent must also be confirmed. The stability wait times out after 4 hours.
- `SCAN`: run the current main-window transport scan (instruments, harmonics, ranges, …), choosing its run folder and file name each time (one run folder per SCAN, with its live CSV and log; the 6221 is zeroed at the scan's end, 2026-10-18); the next line runs only after the scan ends. Preceding TEMP/FIELD sync the matching main-window targets; a TEMP also sets the temperature rate box, to its own rate, or without one to 1 K/min for targets below 10 K and 10 K/min otherwise, as typing the target does. The scan mode and other parameters still come from the main window. A SCAN line runs the same Start, so each reading waits max(Settle s, 1 x the slowest lock-in time constant) and the confirmation shows that wait (2026-10-14; 7 x TC until the user chose 1 x TC that day).

TEMP/FIELD do not wait for stability by themselves; use an explicit WAITFOR. A scan error, cancelled save, stop or command error stops the whole Macro. Stop does not revoke temperature/field setpoints already sent. While running, the editor and main window are locked; Stop and the immediate output-off button remain available.

This is a restricted transport-command format, not a full MultiVu/SQUID seq interpreter. VSMDF, VSMMH and VSMMT are not supported; loading a raw MT.seq/MH.seq reports the line number and does not skip unknown commands and continue. There is no Python eval and no arbitrary system command execution.

He trend: a separate read-only timer records a valid new reading about every 10 s (instrument response time extra) and keeps the whole history of the current session; the x-axis is minutes since the first record and grows automatically. Invalid/stale readings are not filled in; a long interruption shows as a break. The record is in memory only and is lost when the program closes.

Verification: offline unit tests; this is not Windows GUI or real-hardware acceptance.
