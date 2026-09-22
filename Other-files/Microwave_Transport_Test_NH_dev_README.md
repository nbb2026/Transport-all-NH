# Microwave_Transport_Test_NH_dev

A standalone fixed-current two-dimensional frequency-sweep program. The entry point is `Microwave_Transport_Test_NH_dev.py` in the same folder; it does not import the main combined program and contains no other measurement modules. The combined program is unchanged.

## Usage

Run in the instrument Python environment on the original experiment PC:

```console
python Microwave_Transport_Test_NH_dev.py
```

Simulation mode without instruments:

```console
python Microwave_Transport_Test_NH_dev.py --demo
```

In simulation mode the connect buttons must still be clicked, but all connections and readings are simulated; the title shows SIMULATION and the saved settings record simulation=true.

1. Connect the KEPCO 4886, LS475, SMB100A and the selected lock-in (MFLI or SR830).
2. Enter the fixed current (A), compliance (V), microwave power (default 18 dBm) and the two frequency sets.
3. Microwave GHz accepts `1:3:0.1` and descending ranges such as `3:1:-0.1`. The range includes the end point; if the step does not divide evenly the last segment is shorter. The modulation frequency takes only integer start and end values, default 5–400 Hz, and automatically uses all prime numbers in the closed interval (default 76 points, 5–397 Hz). If the range contains no prime, the program asks for a change and does not start.
4. Confirm wiring and output conditions, start, and choose the save folder. Each run creates its own time-stamped subfolder.

At scan start the KEPCO output is initialised first; after a wait the same fixed current is written again separately on the Tk main thread via set_scan_current, then the program waits and reads the field. Afterwards the current is never adjusted from LS475 readings and the field is not swept. Once connected, the LS475 field updates about once per second when idle and about every 0.5 s during scan waits; the actual interval depends on instrument communication. The displayed current is the setpoint, not a measured feedback. LS475 values are converted to G in the meter's current unit without extra zero correction.

## Connections keep the original STFMR settings

| Instrument | Default address |
|---|---|
| KEPCO4886 | GPIB0::6::INSTR |
| SR830 | GPIB0::9::INSTR |
| LS475 | GPIB0::15::INSTR |
| MFLI | dev4435 |
| SMB100A | GPIB0::28::INSTR |

The original VISA and MFLI/LabOne (including legacy bridge) controllers and commands are kept. The KEPCO still uses the original write_raw protocol without terminator and no added queries. SMB modulation uses the original LFO/AM configuration; changing the modulation frequency still sends `LFO:FREQ … Hz`. The reference-input wiring is unchanged; the UI can select the reference input and autorange.

Runtime dependencies are Tkinter, NumPy and Pillow; real instruments additionally need the original PyVISA/VISA driver and the matching Zurich Instruments/LabOne environment. Copying the main `.py` alone is enough to run; the support folder is not needed, but the external runtime used by the legacy LabOne bridge must remain.

## Scan and images

The modulation frequency is the outer loop and the microwave frequency the inner loop. After each change the program waits for the largest of 10 time constants, 5 modulation periods and the set point wait, then discards two valid transition reads. The SR830 keeps its original 1 s time constant; the MFLI time constant is an input. After an autorange adjustment the wait restarts; exceeding the retry limit, losing the reference or an overload stops the scan and keeps the saved data.

The three lower plots show X, Y and Theta. The x-axis is microwave frequency in GHz and the y-axis modulation frequency in Hz; each list entry occupies one equal-width/equal-height cell, i.e. a discrete parameter grid, not an interpolation on non-uniform coordinates. X/Y use a signed colour scale (µV), Theta a cyclic −180° to 180° scale. Grey cells are not yet acquired. Click a cell to see its parameters, lock-in signal and field.

X/Y are the mean of several reads per cell; Theta is `atan2(mean(Y), mean(X))`, avoiding errors from averaging phases directly across ±180°. The meter's raw Theta of each read is also saved separately. Zero-amplitude cells have an empty phase.

The Theta colour bar is labelled −180, −90, 0, 90, 180° in the UI and the PNG; the X/Y colour-bar ticks are unchanged. On 2026-09-06, 25 offline tests (including tick positions and PNG export) passed under Python 3.9.6; no Windows acceptance was done.

## Saving and stopping

- Time-stamped CSV: per-point raw X/Y/R/Theta, frequencies, reference status, field before/after, current setpoint and timestamp, flushed to disk per point.
- JSONL: parameters, range changes, scan progress and end/error records.
- `cells.csv`: means of completed cells.
- `X_V_matrix.csv`, `Y_V_matrix.csv`, `Theta_deg_matrix.csv`: rows are modulation frequencies, columns microwave frequencies; incomplete cells are empty.
- Three `*_heatmap.png`: 4200 × 2700 pixels, 900 DPI.
- `settings.json`: the settings of this scan.

After completion, stop or error the RF is switched off first, then the KEPCO is zeroed/switched off; each instrument is cleaned up separately and a failure is reported clearly. Stop waits for the instrument communication in progress to return. Output-off uses the original controllers and is not verified with additional queries. Raw samples of partial cells are kept but not filled into the completed-cell heat maps.

## Local verification and maintenance

Offline unit tests passed under Python 3.9.6 and 3.11.9, plus a simulated Tk UI check under 3.11.9; no real instruments were connected and no Windows or hardware acceptance was done. No EXE was compiled or packaged.

```console
python microwave_test_support/test_microwave_transport.py
```

The support folder keeps the controller snapshot, source SHA256, application body and tests. After editing `app_body.py.txt`, run `build_standalone.py` to generate the main file; run `extract_controllers.py` to re-extract the controllers from the combined program only when the drivers are deliberately updated.

A green instrument indicator means the controller session is connected, grey means not connected; no extra hardware probe commands are sent. The lock-in reference and range indicators update during scan reads, wait for a new reading after a frequency change, and turn grey after the end or a lock-in change. Green means locked/range OK, yellow waiting for lock/autoranging, red reference lost/overload, grey no reading yet or status unavailable. MFLI range status comes from the device overload flags, SR830 from the LIAS input/filter/output overload bits; a missing status is never shown as normal.

The start dialog shows the estimated duration (wait-time basis): two start-current waits + the settle wait after each cell change + one point wait between the two transition reads + the point wait before each saved sample. It is computed from the selected lock-in's time constant and excludes instrument configuration, communication, autorange/lock retries and file saving, so the real duration is usually longer.

2026-09-06 current-start fix: the original 4886 command protocol is kept and an independent main-thread rewrite of the same setpoint was added. The log gained kepco_initialize_requested, kepco_initialized, kepco_reapply_requested, kepco_reapplied and kepco_field_after_reapply, recording the field before setting, after initialisation and after the rewrite, plus the executing thread. A successful write only means the command call completed, not a measured hardware current. 24 offline tests and the simulated Tk main-thread scheduling passed; the actual current and field effect still need checking on the experiment PC.
