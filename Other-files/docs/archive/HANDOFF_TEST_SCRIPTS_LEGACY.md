# Python MOKE–electrical-transport experiment control project handoff

> Archive note: this describes the early standalone test-script environment; paths may have changed. For the current folder
> layout see [`../../README.md`](../../README.md).

> Updated: 2026-08-04 (Asia/Singapore)  
> Folder at the time: `D:\user data\Niu Heng\Python code\Electrical meters` (experiment PC)  
> Written for a new maintainer without historical context. Read the whole document before changing or running code.

## 1. Project overview

This folder is a set of Python control scripts for laboratory hardware, used for:

- PCO camera live imaging and MOKE difference-image observation;
- image drift correction, rotated ROI selection and ROI mean-intensity analysis;
- Keithley 2410 voltage output, resistance measurement and I–V scans;
- Keithley 6221 DC/AC current output;
- Stanford Research Systems SR830 lock-in readings;
- Keithley 2182/2182A nanovoltmeter live voltage acquisition;
- combined pulse/non-local transport measurement with the 2410, 6221 and SR830;
- debug control of a Thorlabs K10CR1 rotation stage and a serial stepper motor.

It is currently not an installable Python package and has no single entry point. Every executable script must run as a single self-contained file; combined-measurement scripts must contain all instrument-control logic themselves and must not dynamically load, import or execute other `.py` files in the folder.

## 2. Current folder layout

### 2.1 `Camera`

| File | Purpose | Status |
| --- | --- | --- |
| `Camera_lv.py` | PCO camera raw 16-bit live preview with zoom, pan and field-of-view navigation | implemented; needs PCO hardware verification |
| `Camera_lv_sub.py` | background subtraction, translational drift correction, rotated ROI, live ROI intensity curve | implemented; needs PCO hardware and sample-image verification |
| `Camera_lv_sub_2410.py` | combines difference MOKE imaging with a 2410 voltage scan, plots intensity–time and intensity–voltage and saves CSV | the most complete MOKE–voltage combined entry so far; needs combined hardware verification |
| `Thorlabs_control.py` | controls the Thorlabs K10CR1 rotation stage through the Kinesis .NET API | interactive control implemented; needs verification with the matching serial number |
| `Stepper_control.py` | lists the baud-rate, terminator and homing-command combinations once considered | changed to a safe dry run; sends no bytes to the serial port |

### 2.2 `Electrical meters`

| File | Purpose | Status |
| --- | --- | --- |
| `NonLocal_NH_dev.py` | standalone Non-local combined entry with **IV** and **VI** modules in a Notebook. IV sweeps the 6221 AC current; VI cycles "2400 resistance gate → 2400 voltage pulse → 6221 fixed AC current → SR830 reading → 6221 OFF → 2400 resistance again". Both modules can select SR830 #8, #9 or both and set address, AC/DC, harmonic and X/Y/R channel per instrument; the 6221 Trigger Link output line is a text entry (integer `1–6`) in both modules, read back with `PMAR:OLIN?` after sending and written to the CSV. The 6221 Phase Marker is a TTL pulse, so the SR830 is fixed to external reference `FMOD 0` + TTL Falling `RSLP 2`, matching the locking `MOKE_NH_dev.py`; do not change it to Sine zero crossing. In VI the 2400 actions and SR830 queries have a software/hardware double interlock. The folder is chosen only when formal recording starts; raw, summary and VI resistance-gate CSVs and plots are written continuously; Stop, disconnect, window close and exceptions all switch the 2400/6221 outputs off | passed syntax, standalone import, external-reference configuration comparison, dual-SR830 VI acquisition/CSV routing, Trigger Link line propagation and column alignment, negative-resistance absolute value, interlock and safe-shutdown simulations; needs 2400+6221+two SR830 hardware acceptance |
| `MOKE/6221_dual_SR830_IV_sweep.py` | standalone 6221 dual lock-in scan; fixed `GPIB0::8::INSTR` measures `1ω` and `GPIB0::9::INSTR` `2ω`, both share the 6221 external reference and are read sequentially in the same sample period; each channel autoranges independently, records reference/overload status and mean, standard deviation and standard error, the raw CSV stores both instruments and their read-time difference on one row; one figure shows both R/θ time plots, 1ω–I, 2ω–I and 2ω–I² summaries; pre-condition/settling data go to raw only, not the formal mean; Stop/close safely shut down the 6221 and all VISA sessions | passed cache-free syntax check, CSV column alignment, independent single-step range up/down per channel and 3×3 figure refresh simulations; needs hardware synchronisation verification once the same 6221 Trigger Link reference is reliably distributed to both SR830s |
| `2410_output.py` | Tkinter GUI; 2410 autorange resistance measurement, I–V scan, live readings and plots | implemented; needs hardware and safe-load verification |
| `6221_output.py` | Tkinter GUI; 6221 DC/AC current, frequency, compliance, phase marker control and status readback | implemented; needs hardware verification of waveform amplitude and trigger routing |
| `2182_read_voltage.py` | 2182/2182A single-channel live voltage reading, Matplotlib plot and CSV logging | implemented; needs hardware verification |
| `SR830_read.py` | SR830 X, Y, R, θ and status reading with live plots | implemented; needs hardware verification of reference source and range settings |
| `MOKE\SR830_standard_test.py` | self-test following item 10 "Input Noise" of the SR830 manual: A input directly on a 50 Ω terminator (SINE OUT not connected to A), Internal 1 kHz, 1ω, A/Float/AC, Low Noise reserve, 100 nV, 100 ms, 12 dB/oct, reads Channel 1 X Noise (V/√Hz) and, once settled, judges against the official 8 nV/√Hz limit; live preview without saving right after connecting, folder chosen and preview cleared only after **Start Formal Recording**, then formal raw/summary CSV and PNG saved; restores the original settings and returns to Local at the end | passed syntax, official-setting readback/restore, preview/formal data isolation and output simulations; needs SR830 hardware verification with a 50 Ω BNC terminator |
| `MOKE\SR830_read.py` | standalone read-only monitor; uses only `SNAP? 3,4` to show the SR830's current R amplitude and θ phase live in two plots, with automatic V/mV/µV/nV units; preview without saving after connecting, folder chosen and preview cleared only after **Start Formal Recording**, then raw/summary CSV and PNG saved continuously until Stop/close; changes no measurement parameter, closes VISA and returns to Local at the end | passed syntax, static check of setting commands, preview/formal isolation, Stop and Local full simulations; needs SR830 hardware verification of display refresh |
| `MOKE\SR830_dual_address_test.py` | dual-SR830 identification tool; scans all VISA resources using only `*IDN?`, and adds read-only `HARM?`, `FREQ?`, `SENS?` queries for confirmed SR830s; the window highlights both SR830s' VISA address, serial, firmware and current parameters, with copy address and rescan, and changes no instrument setting | passed syntax, no-write static check and a "two SR830 + one 6221" simulated identification; needs confirmation of addresses and serials on the dual-SR830 hardware |
| `PPMS1\6221_SR830_IV_sweep.py` | at start a Tkinter dialog takes the 6221 AC RMS current start, stop, step, waveform frequency, SR830 target sample rate and per-point sampling time; the mean voltage per point is computed, plotted and saved as I–V data | implemented; needs two-instrument, safe-load and reference-wiring verification |
| `PPMS1\6221_SR830_2400_VI_sweep.py` | 2400 voltage pulse + 6221 AC probe current + SR830 readout; at start takes the 6221 current/frequency, SR830 harmonic and 2400 scan range, with hardware-readback interlocks, pre-run reading isolation, overload autorange, absolute-resistance gating and raw/summary/resistance-gate CSVs | implemented and passed simulated fault-interlock and data-isolation tests; the user has run it on hardware, but systematic safe-load acceptance is still needed |
| `PPMS2\6221_SR830_IV_pulse.py` | short positive/negative current pulses from the 6221 internal ARB waveform, then AC read current and SR830 measurement of the post-pulse response | upgraded for the 6221 SCPI limits; the real 30 μs waveform still needs oscilloscope verification |
| `PPMS2\6221_SR830_IV_pulse_踩坑总结.md` | records the SCPI, negative-amplitude, single-cycle output-state and microsecond-timing pitfalls of short 6221 pulses | must be read before changing the pulse program |
| `keithley_2410_resistance.csv` | existing 2410 resistance data | data file, not a program dependency |
| `prime_numbers.txt` | text file with no obvious link to the main measurement flows | purpose to be confirmed |
| `__pycache__` | Python bytecode cache | not source or a formal dependency |

### 2.3 `Test`

| File | Purpose | Status |
| --- | --- | --- |
| `Visa_list.py` | enumerates the current VISA resources | basic diagnostic tool |
| `visa_device_check.py` | opens a VISA address and tries common identity/status queries | communication diagnostic tool |
| `Camera_check.py` | opens the PCO camera and shows name, serial and interface | camera connection check |
| `6221_close.py` | aborts the 6221 waveform and switches the output off | emergency/clean-up helper |
| `6221_SR830_read.py` | 6221 fixed AC current + SR830 second-harmonic reading, live plot and CSV | implemented; needs two-instrument verification |
| `6221_SR830_ramp.py` | sweeps the 6221 AC RMS current and reads the SR830, live plot and CSV | implemented; needs two-instrument verification |
| `6221_SR830_2410_read.py` | 2410 write pulse + 6221 AC probe current + SR830 readout, live plot and CSV | the main three-instrument combined transport script so far; needs three-instrument verification |

## 3. Hardware resources and defaults

The code currently uses these hard-coded addresses:

| Instrument | Default resource / identifier |
| --- | --- |
| Keithley 2410 | `GPIB0::24::INSTR` |
| Keithley 2400 | `GPIB0::24::INSTR` (new three-instrument script; either this or the 2410 script) |
| Keithley 6221 | `GPIB0::30::INSTR` |
| SR830 | PPMS1：`GPIB0::8::INSTR`；PPMS2 pulse：`GPIB0::9::INSTR` |
| Keithley 2182/2182A | `GPIB0::7::INSTR` |
| serial stepper motor | `COM4` |
| Thorlabs K10CR1 | serial `55194494` |

These values are only the lab defaults in the current code. They may change with a different PC, GPIB controller, instrument address or USB port. Before running an output script, verify the resources in NI MAX or an equivalent VISA tool and confirm the identity with `*IDN?`.

Main default measurement parameters:

- 6221 AC：`0.1 mA RMS`、`13.7 Hz`、compliance `50 V`、marker phase `0°`、Trigger Link line `4`；
- 6221 + SR830 fixed measurement: `0.1 mA RMS`, `13.7 Hz`, second harmonic, sample interval `1 s`, total `60 s`;
- 6221 ramp: `0.1–0.5 mA RMS`, step `0.1 mA`, `10 s` per point;
- 6221 + SR830 I–V: the program first asks for an output parent folder and creates the run folder `6221_SR830_IV_sweep_Results_<timestamp>` there; the UI then takes start, stop, positive step, 6221 waveform frequency, SR830 target sample rate, per-point sampling time and harmonic (default `2ω`); the harmonic is a manual entry, non-integers are rounded normally and made absolute, and the result must lie in the SR830-supported `1–19999`; defaults `0.1–3.0 mA RMS`, step `0.1 mA`, `317.3 Hz`, `1 Hz` sampling (`1 s` interval), `30 s` per point; before the formal scan a `30 s` pre-run at `0.1 mA RMS` writes rows tagged `preconditioning` to the raw CSV and keeps them in the cumulative time plot, but explicit phase filtering keeps them out of the I–V mean, I–V curve and summary; ascending or descending scans are supported; each formal point first settles `3 s`; the live figure shows all `R–time` cumulatively top-left and all `θ–time` bottom-left without clearing between current points, with I–V on the right; figure title, axis labels and status bar show the current harmonic, which is also recorded in the raw/summary CSVs; at start the SR830 front-panel Channel 1 is set to `R` and Channel 2 to `θ` and read back; when an `LIAS?` overload bit is seen, `SENS` is raised one level per sample up to index 26; the I–V y-axis defaults to SR830 `R`; the summary CSV also records the achieved sample rate, number of autorange changes and final sensitivity index;
- the 6221 + SR830 I–V live figure keeps a separate gap between the overall title and the top axes; the overall title,
  the I–V subplot title and the bottom status bar all show the 6221 frequency readback;
- with SR830 harmonic `2ω`, the right-hand curve uses the square of the 6221 RMS current readback
  as x-axis in `mA²`; other harmonics keep `mA RMS`;
- 2400 + 6221 + SR830 (current entry `PPMS1\6221_SR830_2400_VI_sweep.py`): 2400 at `GPIB0::24::INSTR`, 6221 at `GPIB0::30::INSTR`, SR830 at `GPIB0::8::INSTR`; the program first asks for an output parent folder, then the GUI takes the 6221 AC current (`mA RMS`, default `0.1 mA`), frequency (default `317.3 Hz`), SR830 harmonic, and the 2400 start voltage, stop voltage and positive step; the scan order is "start→stop→start", default `+20 V→-20 V→+20 V`, step `4 V`, software absolute limit `20 V`; the 2400 software-timed pulse width defaults to `0.1 s`, no SCPI error query or figure redraw happens during the active pulse, and both the requested width and the software-measured interval are saved; after the pulse the 2400 is zeroed/OFF immediately with readback confirmation, then a `0.5 s` wait; each cycle then starts the 6221, and during the `10 s` pre-run the SR830 is already read at `1 s` intervals and shown live, those rows written to the raw CSV as `6221_pre_run` with `included_in_summary=False`, never entering the formal mean, summary or the bottom-right summary plot; the formal phase targets `100` status-normal samples, overload/unlock rows stay only in the raw CSV, on overload `SENS` is raised one level with a `3 s` wait, and acquisition may extend by at most `60 s` to complete the valid samples; if not a single valid sample arrives within the extension, the voltage point is still kept and flagged `FALLBACK_ALL_STATUS_FLAGGED`, never silently dropped; the formal R mean and population standard deviation are plotted bottom-right with `±1σ` error bars, and phase uses a circular mean so `+179°/-179°` is not averaged to `0°`; after the formal read the 6221 is stopped and confirmed OFF, and the 2400 measures resistance with an auto-selected `1 nA–1 mA` test current, `5 V` compliance and 5 averages; resistance is `abs(V/I)`, the raw signs of voltage and current are kept, and only `|R| < 1 MΩ` blocks the next pulse; the robust sigma and the "median − 3σ" lower bound are still written for diagnosis but do not decide release; every resistance measurement (including a first blocked one) is written to a separate resistance-gate CSV; the output folder is `6221_SR830_2400_VI_sweep_Results_<timestamp>` under the chosen location, with raw CSV, summary CSV, resistance-gate CSV and PNG; during the SR830 read phase no possibly-timing-out `READ?` is sent to the switched-off 2400, only `OUTP?` to confirm OFF; before the first run on a new sample, the voltage, current, compliance and the `1 MΩ` gate must be re-confirmed as safe;
- 2410 I–V GUI: default `0–5 V`, step `0.5 V`, current compliance `1 mA`;
- MOKE + 2410: default scan from `+1 V` to `-1 V`, step `0.1 V`, software absolute limit `10 V`, `2 s` wait then `10 s` acquisition per point;
- PCO difference-imaging exposure: `0.20 s`; the raw preview script uses `0.02 s`;
- 2182: channel 1, NPLC 1, autorange, sample interval `1 s`.

Check every one of these before a real experiment; do not treat defaults as sample-safe values.

## 4. Main measurement flows

### 4.1 PCO difference MOKE + 2410 voltage scan

Entry: `Camera\Camera_lv_sub_2410.py`

1. connect the Keithley 2410 and the PCO camera;
2. configure the camera and grab one background frame;
3. estimate the translational drift of each frame against the background;
4. correct the drift, subtract the background and show the difference image;
5. the user drags a rotated rectangular ROI and can adjust angle, size and position;
6. the user starts the scan and enters start, stop and step;
7. each voltage point first settles, then the ROI mean intensity is accumulated during acquisition;
8. per-frame intensity–time data and per-voltage mean, standard deviation and sample count are saved;
9. on completion, manual stop or exit the 2410 is set to `0 V` and its output switched off.

Note: the background frame is grabbed only at start. If the optics, focus, exposure, sample position or illumination change noticeably, restart or implement a background refresh.

### 4.2 2410 + 6221 + SR830 combined measurement

Entry: `Test\6221_SR830_2410_read.py`

1. connect and initialise the three instruments;
2. the 2410 applies a voltage pulse of the given width to the sample and returns to `0 V`;
3. wait the set settle time;
4. start the 6221 AC probe current;
5. read the 6221, SR830 and 2410 state in a loop for the set time;
6. write the CSV row by row while updating the live curves;
7. compute the mean SR830 `R` for each write pulse;
8. stop the 6221 and move to the next pulse point;
9. in `finally`, zero the 2410 and switch its output off, abort the 6221 waveform and close the VISA resources.

### 4.3 2400 + 6221 + SR830 interlocked combined measurement

Entry: `Electrical meters\PPMS1\6221_SR830_2400_VI_sweep.py`

1. connect the three instruments and strictly confirm the models with `*IDN?`;
2. stop the 6221 waveform and confirm the hardware readback `6221 OUTP? = 0` before entering the
   `2400_VOLTAGE` interlock phase;
3. the 2400 switches on from `0 V` and applies one voltage pulse; any SR830 read call in this phase
   is refused by the interlock before a VISA query is sent;
4. after the pulse the 2400 is forced back to `0 V` with `OUTP OFF`, then the hardware readback
   `2400 OUTP? = 0` is confirmed;
5. the `0.5 s` post-pulse wait happens in `IDLE`, still without SR830 queries;
6. the 6221 AC waveform starts, `2400 OUTP? = 0` is confirmed again, then
   `SR830_READ` begins; `10 s` of pre-run data are read continuously, shown live and written to the raw CSV,
   but explicitly excluded with the `6221_pre_run` phase and `included_in_summary=False`;
7. after the pre-run the formal timer restarts and the SR830 X, Y, R, θ,
   reference frequency and `LIAS?` are recorded while the 6221 is read back and the 2400 output is continuously confirmed OFF;
8. the formal phase targets 100 status-normal samples; overload/unlock samples stay in the raw
   CSV but not in the mean, overload raises the range one level with a `3 s` wait, and at most
   `60 s` of extra acquisition tops up the samples; at the extension limit the voltage point must not be deleted but flagged;
9. after the formal acquisition the 6221 is stopped and confirmed OFF, and the 2400 measures resistance once with an auto-selected test
   current; `abs(V/I)` is used and `|R| < 1 MΩ` blocks the next pulse; the SR830 is not queried during the resistance
   phase, and voltage-pulse mode is restored afterwards;
10. the formal R mean, population standard deviation and 2400 resistance go to the summary CSV; the 2400
    resistance plot is middle-right and the SR830 mean with `±1σ` error bars bottom-right;
11. closing the figure, Ctrl+C, normal completion or an exception all zero/switch off the 2400,
   abort/zero/switch off the 6221 and stop the SR830 software polling.

The SR830 has no general SCPI command to stop its internal DSP demodulator, so "stop measurement" here
strictly means: no SR830 measurement queries are sent and no SR830 data recorded during the 2400 voltage and settling phases;
it must not be read as the SR830 front panel or internal DSP stopping.

## 5. Software dependencies and runtime environment

Use a Python 3.9 environment matching the existing bytecode or otherwise verified. Main dependencies:

```text
numpy
opencv-python
pco
Pillow
pyvisa
matplotlib
pythonnet
```

Also required:

- NI-VISA or a compatible VISA backend;
- the PCO camera driver and an SDK matching the `pco` Python package;
- Thorlabs Kinesis for `Thorlabs_control.py`;
- a Windows GUI environment, since several programs rely on Tkinter, OpenCV windows or the Matplotlib GUI.

The project has no `requirements.txt` and no record of verified exact package versions. Record the environment versions on the actually working experiment PC before creating a dependency list.

### 5.1 Single-file rule

- An instrument script may depend on the Python standard library and installed third-party packages such as `pyvisa` and `matplotlib`, but not on another project source file.
- `importlib`, `runpy`, `exec` or `sys.path` changes to load measurement scripts from the same folder are forbidden.
- After copying one `.py` alone to the experiment PC, it must start as long as the third-party packages, VISA backend and instrument drivers are installed.
- Changes to shared logic must be synchronised into every standalone script, not "shared" through a new runtime script dependency.

### 5.2 Two-stage formal-recording rule for new measurement programs

Newly written or substantially rewritten acquisition programs follow this flow:

1. After start, only read parameters and connect instruments, entering `READY`/preview; no output folder is requested and no result folder, CSV or PNG is created.
2. The main figure must have a **Start Formal Recording** button. The folder chooser appears only after it is clicked.
3. Cancelling the folder chooser keeps `READY`/preview without creating empty files.
4. Starting formal recording clears the preview statistics, resets the formal time origin and explicitly keeps preview/pre-run data out of the formal mean, error bars, summary and summary plots.
5. Pressing Stop or closing the figure before **Start Formal Recording** creates no result files, but instrument safe-stop and configuration restore must still run.
6. Once formal recording has started, Stop, window close, normal completion or an abnormal exit must flush/close files and save existing data, then ramp the sources to zero/off, stop software polling, restore instrument settings and close VISA.
7. Scan/pulse programs unsuited to preview must still stop in a "connected but not outputting" `READY` state and must not start the source or scan before the formal button is pressed.

### 5.3 Result folder rule

Except for the per-run folders noted below, automatically generated CSV, PNG and log files are saved next to the
source file in:

```text
<source file name without .py>_Results
```

For example:

```text
Electrical meters\6221_SR830_IV_sweep_Results
Electrical meters\2182_read_voltage_Results
Camera\Camera_lv_sub_2410_Results
Test\6221_SR830_read_Results
```

The folder is created on first save, so starting the program from a different PowerShell working directory does not
move the results.

`Electrical meters\PPMS1\6221_SR830_IV_sweep.py` and
`Electrical meters\PPMS1\6221_SR830_2400_VI_sweep.py` create a separate time-stamped folder for every run
inside the parent folder chosen at start:

```text
6221_SR830_IV_sweep_Results_YYYYMMDD_HHMMSS
6221_SR830_2400_VI_sweep_Results_YYYYMMDD_HHMMSS
```

That run's raw CSV, summary CSV and PNG are saved in the same folder; the three-instrument
program additionally saves the resistance-gate CSV.

## 6. Recommended first-check order

Do not start with the combined output programs. Accept in this order:

1. enumerate the resources in NI MAX and record each instrument's VISA address;
2. run `Test\Visa_list.py`;
3. confirm each instrument's `*IDN?` with read-only queries;
4. run `Test\Camera_check.py` to confirm the PCO camera connection;
5. test the read-only programs such as `SR830_read.py` and `2182_read_voltage.py`;
6. test single-instrument 2410 and 6221 control with a safe load and minimum output;
7. verify that the stop button, window close, Ctrl+C and exception paths really switch the outputs off;
8. then test 6221 + SR830;
9. finally test the 2410 + 6221 + SR830 or PCO + 2410 combined flows.

Without hardware evidence the status can only be stated as "code implemented, static syntax check passed, hardware pending".

## 7. Safety constraints that must be kept

### 7.1 Do not confuse RMS and peak

The 6221 waveform amplitude is a peak value, while the UI and most experiment parameters use RMS:

```text
I_peak = sqrt(2) × I_rms
I_rms  = I_peak / sqrt(2)
```

For example `0.1 mA RMS` must be written as about `0.141421 mA peak`. Removing this conversion causes a 41.4% amplitude error.

### 7.2 Stop must abort the waveform and switch the output off

A safe 6221 stop must include:

```text
SOUR:WAVE:ABOR
CURR 0
OUTP OFF
```

A safe 2410 exit must at least zero the source voltage and execute `OUTP OFF`. Any refactor must cover normal completion, window close, keyboard interrupt and abnormal exit.

The current UI scripts follow these exit conventions:

- closing the Tkinter or Matplotlib main window stops acquisition; it does not continue in the background;
- in multi-window OpenCV programs, closing any display window exits the acquisition loop;
- the 2410 first zeroes the active source level, then switches the output off, and resets when the UI finally closes;
- the 6221 first aborts the waveform, sets the DC source level to `0 A`, switches the output off, then resets;
- the 2182 first stops the animation callback and sends `ABOR`, then closes VISA;
- the SR830 has no SCPI equivalent of "stop measurement"; closing the UI means stopping the
  Python polling and closing the VISA session, and must not be claimed to stop the lock-in's internal signal processing;
- camera programs stop acquisition, and the Thorlabs command-line program stops motion, stops polling
  and disconnects on exit.

### 7.3 Do not mix SCPI commands of different Keithley products

The 6221 waveform mode uses:

```text
CURR:COMP
SOUR:WAVE:FUNC
SOUR:WAVE:FREQ
SOUR:WAVE:AMPL
SOUR:WAVE:ARM
SOUR:WAVE:INIT
```

Do not copy the 2410 SourceMeter's `SOUR:FUNC CURR` into the 6221 waveform
initialisation; the 6221 reports `-113, "Undefined header"`. The 6221 compliance command is
`CURR:COMP`, not `SOUR:CURR:COMP`.

### 7.4 Do not ignore the SCPI error queue

A successful VISA `write()` only means the command was sent, not that the instrument accepted it. The single-instrument controllers already query `SYST:ERR?`; keep doing so for new critical commands and clear old errors at initialisation.

### 7.5 Abort before changing an active waveform

Do not change amplitude, frequency or phase marker while a 6221 waveform is running and assume it takes effect seamlessly. Abort first, change the parameters, and restart if it was running.

### 7.6 Trigger Link must be verified against the actual wiring

The current default marker line is 4. Do not change the line without confirming the 6221 routing, cabling and the external device input. In particular do not casually use line 2, which may conflict with the default trigger output.

The 6221 waveform Phase Marker is a short TTL pulse on Trigger Link, not an analogue
sine reference. Wherever the `SOUR:WAVE:PMAR:STAT ON` output feeds an SR830 `REF IN`,
the SR830 must use `FMOD 0` (External) and `RSLP 2` (TTL Falling), confirmed by reading back
`FMOD?` and `RSLP?`. Do not copy the `RSLP 0` once used by the old
`6221_SR830_2400_VI_sweep.py`; that value means Sine zero
crossing and leaves an SR830 fed directly with Trigger Link TTL permanently UNLOCKed. Trigger Link
lines 1–6 map to connector pins 1–6, and the BNC shield must go to Ground on pin 7 or 8. While the 6221
output is off there is no Phase Marker, so an SR830 UNLOCK display is normal.

### 7.7 No active probing of the unknown serial motor

`Stepper_control.py` only prints candidate combinations and sends nothing to the serial port. Do not restore active homing probes before the controller model, protocol, reliable stop command, mechanical travel, limits and physical emergency stop are confirmed. Ctrl+C cannot stop a motion the controller has already accepted.

### 7.8 High-voltage capability does not mean the sample tolerates high voltage

The software limit in the 2410 GUI is no substitute for an experiment safety assessment. Before any output, confirm the sample, electrodes, probe station, cables, grounding, compliance and the personnel safety envelope.

## 8. Verified scope so far

As of 2026-07-28:

- 23 Python source files were found in the folder; the first full review covered 18, and this round re-checked
  the later added/moved PPMS1 three-instrument script and the PPMS2 pulse script;
- all Python sources pass the static syntax compilation check;
- the relevant commands were checked against the official 6221, SR830, 2182A and 2400-series manuals;
- the `SOUR:FUNC CURR` wrongly used for the 6221 in three old combined scripts was removed and
  `SOUR:CURR:COMP` corrected to `CURR:COMP`;
- the critical initialisation commands of the 6221/2410/2182A query the error queue one by one; the SR830 uses
  `*ESR?` to check command/execution errors, and the GPIB programs send
  `OUTX 1`；
- `visa_device_check.py` identifies the model first and then sends only read-only queries that instrument supports,
  no longer sending the Keithley `SYST:ERR?` to an SR830;
- most scripts save automatic data and images in the results folder next to the source; PPMS1's
  `6221_SR830_IV_sweep.py` and `6221_SR830_2400_VI_sweep.py` first let the user
  choose a parent folder and then create `<source name>_Results_<run timestamp>`;
- simulated fault tests of `Electrical meters\PPMS1\6221_SR830_2400_VI_sweep.py` confirmed:
  no SR830 query is sent while the 2400 output stays ON; no `OUTP ON` is sent to the 2400 while the 6221 output
  stays ON; an SR830 overload raises `SENS` one level;
  pre-run samples are shown and written to the raw CSV but do not enter the formal mean; a persistent fault does not delete
  the whole voltage point; the bottom-right `±1σ` error bars refresh continuously; a negative resistance is first made absolute, and
  the screenshot case of `|R|=5.07593 MΩ` with a negative diagnostic lower bound is released correctly by
  `|R| >= 1 MΩ`;
- no lab hardware was connected in this handoff;
- there is no evidence that every SCPI command, camera SDK call, Kinesis call or serial command has passed fully on the current hardware;
- the working folder was not recognised as a Git repository, so branch, commit history and status were not checked;
- the project has no README, `requirements.txt`, automated tests, unified configuration file or unified launcher.

Do not describe "syntax check passed" as "experimentally verified".

## 9. Known technical debt and potential problems

1. VISA addresses, camera parameters, scan parameters and device serials are hard-coded across the scripts;
2. the camera programs use many module-level globals, which makes extension and testing hard;
3. several script names are still exploratory/test-like; for formal PPMS1 6221–SR830 I–V measurement prefer
   `Electrical meters\PPMS1\6221_SR830_IV_sweep.py`;
4. the results folder is unified, but file names, column names and experiment metadata formats still differ between scripts;
5. there is no unified data file naming, metadata, sample ID or experiment parameter record format;
6. there is no simulated instrument layer, so state machines and exception clean-up cannot be tested without hardware;
7. some read errors are replaced by `NaN` and execution continues, which suits long acquisitions but may hide persistent communication faults;
8. PCO drift correction handles translation only, not rotation, scale, deformation or illumination drift;
9. the PCO background frame is grabbed at start; there is no re-capture button while running;
10. Tkinter, OpenCV and Matplotlib use different GUI event loops, which needs care in a future merge;
11. the purpose of `prime_numbers.txt` is unclear;
12. no dependency-version record exists for reproducing the experiment environment.

## 10. Suggested order of follow-up work

1. complete the read-only connection checks and record every device's model, serial, firmware and VISA address;
2. verify output, readback, compliance and shutdown per instrument with a safe load;
3. verify the 6221 RMS/peak conversion and the phase-marker line, phase and polarity;
4. verify the SR830 reference source, harmonic, sensitivity, time constant and output units;
5. verify that MOKE drift correction and the ROI intensity sign match the actual magnetisation reversal;
6. save one manually checked raw dataset and experiment log for each combined flow;
7. add a unified configuration file and move VISA addresses and common parameters out of the source;
8. add a `requirements.txt` or locked environment file and a short README;
9. unify CSV file names, column names, sample information and parameter metadata;
10. gradually split instrument communication, measurement state machines, GUI and data saving into testable modules;
11. after hardware verification, write the results, dates, wiring and failed items back into this document and put it under version control.

## 11. Hardware test record template

Append the following to the end of this document for each acceptance:

```text
Date:
Operator:
Script and version/commit:
Instrument models, serials, firmware:
VISA addresses:
Wiring notes:
Sample / safe load:
Output limits and compliance:
Test items:
Passed:
Failed:
Raw data path:
Outputs off after abnormal exit:
Notes:
```

## 12. Definition of done

For a stable handoff the project must at least meet:

- the main scripts pass the test checklist on the target experiment PC and its hardware;
- every output script reliably switches outputs off on normal stop, window close and abnormal exit;
- the actual 6221 current, frequency and phase marker have been verified with meters;
- the 2410 output, readback, compliance and pulse flow have been verified with a safe load;
- the meaning, range, units and sampling settings of the SR830 and 2182 readings are confirmed;
- the PCO background, drift correction, ROI and intensity–magnetisation relation are verified with real images;
- data from combined measurements carry enough metadata to be traceable and reproducible;
- dependencies, address configuration and how to run are formally documented;
- code and documents are under version control with the hardware-verified commit recorded.

## 13. Pitfalls found 2026-07-26 to 2026-07-28 and current rules

This section records problems actually exposed recently in the PPMS1 three-instrument measurement, PPMS2 short pulses and Windows
deployment. Later changes must not look only at the final code; the reasons for these constraints must be understood.

### 13.1 The 2400's `0.1 s` software pulse was stretched by queries and plotting

Early implementations placed the checked write, `SYST:ERR?`, status readback and Matplotlib redraw inside
the interval where the 2400 output was ON. Although the code said `sleep(0.1)`, the time the port actually held the target
voltage also included GPIB round trips and GUI refresh, so it was clearly longer than requested.

Current rules:

- complete the target-voltage configuration and every possible check before the output is switched on;
- the active pulse interval runs only `OUTP ON → sleep(width) → SOUR:VOLT 0 → OUTP OFF`;
- no error-queue query, instrument readback or figure refresh inside the active pulse;
- query errors and confirm `OUTP? = 0` only after zero/OFF;
- raw/summary record both the requested pulse width and the software-measured actual pulse width.

The `0.1 s` is still operating-system software timing, not a hardware guarantee. Experiments sensitive to pulse width
must be verified with an oscilloscope, not trusted to the software timestamps in the CSV.

### 13.2 The 2400 may return a negative `V/I`; the gate must use the absolute resistance

On hardware, a positive-resistance sample was once returned as negative by the 2400, usually from the sign/polarity combination of
the measured voltage and current. Comparing the negative number directly with a positive threshold wrongly blocked every later pulse.

Current rule:

```text
R_gate = abs(V / I)
R_gate < 1 MΩ   -> block the next pulse and shut down safely
R_gate >= 1 MΩ  -> allow the next pulse
```

The raw signs of voltage and current are still saved for diagnosis; the resistance plot, summary and gate use the positive
`abs(V/I)`. The robust sigma and `median - 3σ` lower bound are still saved but only for diagnosis and no longer
decide release. A case of `|R| = 5.07593 MΩ` with diagnostic lower bound `-4.476605 MΩ` occurred; gating on the lower bound
would have stopped wrongly, and that point is now released normally by `|R| >= 1 MΩ`.

### 13.3 A first blocked resistance must also be written to file

If the initial resistance gate raised before a summary point was created, the old flow could print the result only to the terminal,
losing the most important failure evidence. The current program writes every resistance measurement to a separate resistance-gate CSV
before calling the gate check; the first failure, failures in the loop and the final measurement are all traceable.

### 13.4 Read the SR830 during the 6221 pre-run, but never pollute the formal mean

The user asked to see the SR830 response during the `10 s` pre-run after the 6221 starts at each pulse point.
Reading into memory without a phase label easily mixes pre-run transients into the bottom-right summary plot.

Current rules:

- after the 6221 starts, enter the interlock phase that allows SR830 queries immediately;
- R, θ and current readback within the `10 s` are shown live and written row by row to the raw CSV;
- the row phase is fixed to `6221_pre_run` with `included_in_summary=False`;
- the formal `100 s` is re-timed and re-counted after the pre-run ends;
- the summary CSV, bottom-right mean and error bars use formal-phase data only.

Excluding pre-run data by "ignore the first N rows" is not enough; N changes with sample interval, communication delay or range
waits, so the explicit phase field must be used for filtering.

### 13.5 A continuous scan must not lose a whole voltage point because of an SR830 status fault

SR830 `LIAS?` overload/unlock rows should not enter the normal mean, but the user explicitly requires that
an executed voltage point is never dropped silently in continuous measurement. The current strategy:

- status-normal rows count towards the target number of valid samples;
- overload/unlock rows stay in the raw CSV with the exclusion reason;
- on overload `SENS` is raised one level and the SR830 is not queried for `3 s` after the range change;
- formal acquisition extends by at most `60 s` to complete the target valid samples;
- with some valid samples the point is kept and flagged `PARTIAL_VALID_SAMPLES_MAX_EXTENSION`;
- with no valid sample at all the point is still kept using the raw rows and flagged
  `FALLBACK_ALL_STATUS_FLAGGED`, making the quality problem explicit.

This is a "never lose a scan point" data-integrity strategy; it does not mean status-flagged data are equally trustworthy
physically. Post-processing must read `data_quality` and the status counts.

### 13.6 The transient after an SR830 range change must not enter the mean immediately

After `SENS` is raised automatically, the lock-in output needs to settle again. The old implementation queried on the very next loop
and took the range-switch transient as a valid point. Now it waits `3 s` without measurement queries, then
continues topping up valid samples. That wait is a different concept from the normal `1 s` sample interval.

### 13.7 Phase must not use a plain arithmetic mean

When the phase crosses `±180°`, a plain average gives a wrong result, e.g.:

```text
(+179° + -179°) / 2 = 0°      # physically wrong
circular mean             ≈ ±180°
```

The summary now uses the circular mean of sine/cosine vectors. Do not change it back to `np.mean(theta)`.

### 13.8 The bottom-right error bars come only from formal samples

The bottom-right `Mean SR830 R vs pulse voltage` uses the population standard deviation `±1σ` of the formal R samples
as vertical error bars. When updating a Matplotlib errorbar incrementally, the centre line must not be updated while the
caps and vertical bars are left behind; the error-bar container is now rebuilt whenever a summary point is added.
Pre-run rows and status-excluded rows do not enter the mean or standard deviation.

### 13.9 Sending an OFF command is not the same as confirming the output is off

The 6221 `OUTP?` state was seen lagging after a single cycle ended, and on abnormal paths commands had been sent without
evidence of the hardware state. A safe exit must now both act and read back:

- 6221：`SOUR:WAVE:ABOR → CURR 0 → OUTP OFF → OUTP?`；
- 2400: zero the source level, `OUTP OFF → OUTP?`;
- if the readback is not 0 or cannot be confirmed, a shutdown warning must be reported; do not print "safely shut down".

Closing the Matplotlib window, Ctrl+C, normal completion and any exception must all go through the same `finally`
clean-up. An SR830 "stop" still only means stopping Python queries and closing VISA.

### 13.10 The PPMS2 30 μs pulse cannot come from Python `sleep`

Windows scheduling, Python and GPIB round trips cannot control `30 μs` reliably. The PPMS2 pulse
program must use the 6221 internal waveform/ARB engine. The two confirmed wrong commands are:

```text
SOUR:WAVE:PHAS 180       -> -113, "Undefined header"
SOUR:WAVE:AMPL -2.5e-3   -> -222, "Parameter data out of range"
```

The 6221 has no `SOUR:WAVE:PHAS` for inverting a pulse; `PMAR` only controls the Trigger Link
marker and does not change the current direction; `WAVE:AMPL` accepts only positive amplitudes. Positive and negative pulses are currently expressed as two-point
ARB data `[+1, 0]` or `[-1, 0]` with the amplitude as the absolute current. The ARB shape is only
a setpoint; the 30 μs, the rising edge and the real waveform into the load must be verified with an oscilloscope. Full notes:

```text
Electrical meters\PPMS2\6221_SR830_IV_pulse_踩坑总结.md
```

### 13.11 Do not type `pip` at the Python `>>>` prompt when installing packages on Windows

The following, typed at the Python REPL, produce `SyntaxError` or `NameError`:

```text
>>> pip install pyvisa numpy matplotlib
```

Leave `>>>` first and run in PowerShell:

```powershell
py -m pip install --upgrade pip
py -m pip install pyvisa pyvisa-py matplotlib numpy
```

If `py --version` works while `python` shows the Microsoft Store alias, prefer
`py -m pip` and `py script.py`; when configuring PATH, add both the Python install folder and
its `Scripts` subfolder. Do not copy or move `pip.exe` just to silence a warning.

### 13.12 PPMS1 and PPMS2 use different SR830 addresses

The PPMS1 three-instrument script currently uses `GPIB0::8::INSTR` and the PPMS2 pulse script
`GPIB0::9::INSTR`. When copying a script to another PPMS or experiment PC, re-confirm with resource enumeration and
`*IDN?`; never copy an address just because the two instruments are the same model.
