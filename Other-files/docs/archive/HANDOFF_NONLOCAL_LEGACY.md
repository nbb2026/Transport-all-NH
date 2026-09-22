# NonLocal_NH_dev.py complete code handoff

> Archive note: this is the historical record of the standalone Non-local program of 2026-08-04, not the operating notes of the current
> combined GUI; for the current entry point see [`../../README.md`](../../README.md).

> Generated: 2026-08-04 (Asia/Singapore)  
> Script path: `/Users/nbb/Desktop/2-Posdoc_Heng/0-Codex/2-Python-Transport/NonLocal_NH_dev.py`  
> Size: 4808 lines, single self-contained file, no external .py dependencies.

---

## 1. Script overview

`NonLocal_NH_dev.py` is a **standalone laboratory instrument-control GUI program** for non-local transport measurement. It controls, at the same time:

- **Keithley 2400** (GPIB0::24::INSTR) — voltage pulse output + resistance measurement
- **Keithley 6221** (GPIB0::30::INSTR) — AC current source
- **one or two Stanford Research SR830** lock-in amplifiers (GPIB0::8 and/or GPIB0::9)

The script provides 4 measurement tabs in Tkinter; each tab controls its instruments, saves its data and plots independently.

### The four measurement tabs

| Tab | Function | Instruments |
|---|---|---|
| **IV · 6221 current sweep** | sweeps the 6221 AC current (start→stop, step) and reads the SR830 | 6221 + SR830 #8 / #9 |
| **F · 6221 prime-frequency sweep** | fixed current, sweeps only integer prime frequencies in a range, reads the SR830 | 6221 + SR830 #8 / #9 |
| **R · 2400 continuous resistance** | the 2400 alone measures resistance continuously (adaptive source current) with a live plot | 2400 only |
| **VI · 2400 voltage pulses** | 2400 voltage pulse → 6221 AC probe current → SR830 reading → 2400 resistance measurement | 2400 + 6221 + SR830 #8 / #9 |

**Key design principles**:

- Each tab owns its instrument control channel (VISA session), but **the same physical instrument cannot be opened by several tabs at once**.
- Every exit path (Stop button, window close, Ctrl+C, exception) zeroes the instruments safely through `finally` blocks.
- Preconditioning and per-point settling data are written to the raw CSV and kept in the time plot but **do not take part in the summary average**.

---

## 2. Complete class architecture

### 2.1 `VIMeasurementStopped` (exception)

- Carries the stop signal between threads on the VI tab.
- When Stop is pressed, the thread raises it to leave the measurement loop quickly.

### 2.2 `VIMeasurementInterlock` (state machine)

- Prevents 2400 voltage/resistance operations and SR830 queries from touching the same physical instrument at the same time.
- State flow: `IDLE` → `2400_VOLTAGE` / `2400_RESISTANCE` / `SR830_READ` → `IDLE`
- Key methods:
  - `begin(phase)`: enter a new phase, requires IDLE
  - `end(phase)`: finish the phase, back to IDLE
  - `require(phase)`: check that the current phase matches
  - `force_idle()`: force IDLE (exception clean-up)

### 2.3 `Keithley2400VIController` (~280 lines)

**Responsibility**: thread-safe Keithley 2400 controller with two modes:

1. **Voltage pulse mode** (`setup_voltage_mode`):
   - configures `SOUR:FUNC VOLT`, fixed voltage mode
   - sets the current limit (default 1 mA)
   - output elements: `VOLT,CURR`
   - method `apply_voltage_pulse(voltage_v, width_s, stop_event)`:
     - zero first → output on → apply the voltage → wait the pulse width → safe off

2. **Resistance mode** (`_setup_resistance_mode`):
   - configures `SOUR:FUNC CURR`, fixed current mode
   - voltage limit 5 V, NPLC 1.0
   - method `measure_resistance(stop_event)`:
     - adaptive source current: starts at the smallest current and steps up/down from |V|
     - target voltage window: 0.05 V – 2.0 V
     - 5 readings averaged per current level

**Key design**:

- `SYST:ERR?` checks the error queue after every SCPI write
- `*IDN?` must contain "2400" on connect
- every operation is protected by `threading.RLock()`
- `disconnect()` cleans up fully: zero → output off → close the VISA session

### 2.4 `NonLocalACController` (~500 lines)

**Responsibility**: thread-safe 6221 + multi-SR830 controller.

**Connect** (`connect`):

1. open the 6221, `*IDN?` must contain "6221"
2. open the selected SR830s (#8 and/or #9), `*IDN?` must contain "SR830"
3. set `OUTX 1` on each SR830 (external reference output enable)

**Configure** (`configure`):

- 6221 settings:
  - `CURR:COMP {compliance}` — current compliance
  - `SOUR:WAVE:FUNC SIN` — sine wave
  - `SOUR:WAVE:FREQ {frequency}` — frequency
  - `SOUR:WAVE:AMPL {peak}` — amplitude (RMS → peak conversion)
  - `SOUR:WAVE:PMAR {phase}` / `PMAR:OLIN {line}` — Trigger Link phase marker
  - readback verification of frequency, marker line and phase

- each SR830:
  - `FMOD 0` — external reference mode
  - `RSLP 2` — TTL falling-edge trigger
  - `HARM {harmonic}` — harmonic
  - `ISRC 1, IGND 0, ICPL {0/1}, ILIN 1` — input settings
  - `SENS 9, OFLT 10, OFSL 1, RMOD 1, PHAS 0` — sensitivity/filter
  - `DDEF 1,1,0; DDEF 2,1,0` — front-panel display channels

**Autorange logic** (`_auto_range`):

| Condition | Action |
|---|---|
| `LIAS & 0x07` (overload) or utilisation ≥ 85% | range up (SENS + 1), reset the low-range counter |
| utilisation < 15% and SENS > 0 | range down (SENS - 1), needs 1 confirmation |
| otherwise | keep the current range |
| after a range change | 3-sample cool-down (`range_cooldown`) |

**Sample reading** (`read_samples`):

- reads `SNAP? 1,2,3,4,9` from the SR830: X, Y, R, phase, reference_frequency
- reads the `LIAS?` status bits: 1=input overload, 2=filter overload, 4=output overload, 8=reference unlocked
- autorange adjustment + reference validity check

**Safe exit** (`output_off`):

- `SOUR:WAVE:ABOR` → `CURR 0` → `OUTP OFF`
- double confirmation: `confirm_output_off()` requires the physical output to be off

### 2.5 `NonLocalIVApp` (~3500 lines)

**Responsibility**: the Tkinter GUI application with the 4 measurement tabs.

**Thread model**:

| Component | Thread pool | Stop event | Controller |
|---|---|---|---|
| IV Tab | `executor` | `stop_event` | `controller` (NonLocalACController) |
| F Tab | `frequency_executor` | `frequency_stop_event` | `frequency_controller` |
| VI Tab | `vi_executor` | `vi_stop_event` | `vi_ac_controller` + `vi_2400_controller` |
| R Tab | `resistance_executor` | `resistance_stop_event` | `resistance_controller` |

**UI update mechanism**:

- measurement threads send UI callbacks to the main thread through a `queue.Queue`
- the main thread drains the queue every 40 ms via `root.after(40, _drain_ui_queue)`
- status bar, indicators and live plots are updated safely this way

**Data saving**:

- each tab creates a time-stamped subfolder once the user picks an output folder
- formats: CSV (raw + summary) + PNG (live plots)
- preconditioning data carries the `data_quality` field and is excluded from the summary

---

## 3. Complete list of hard-coded constants

### 3.1 VISA resource addresses

| Constant | Value | Meaning |
|---|---|---|
| `NONLOCAL_6221_RESOURCE` | `"GPIB0::30::INSTR"` | Keithley 6221 |
| `NONLOCAL_SR830_8_RESOURCE` | `"GPIB0::8::INSTR"` | SR830 #8 |
| `NONLOCAL_SR830_9_RESOURCE` | `"GPIB0::9::INSTR"` | SR830 #9 |
| `VI_2400_RESOURCE` | `"GPIB0::24::INSTR"` | Keithley 2400 |

### 3.2 IV tab defaults

| Constant | Default | Meaning |
|---|---|---|
| `NONLOCAL_START_RMS_MA` | 0.1 | start current (mA RMS) |
| `NONLOCAL_STOP_RMS_MA` | 1.0 | stop current (mA RMS) |
| `NONLOCAL_STEP_RMS_MA` | 0.1 | current step (mA) |
| `NONLOCAL_FREQUENCY_HZ` | 13.7 | 6221 frequency (Hz) |
| `NONLOCAL_SAMPLE_HZ` | 1.0 | SR830 sample rate (Hz) |
| `NONLOCAL_SETTLE_S` | 10.0 | settle time per point (s) |
| `NONLOCAL_MEASURE_S` | 100.0 | measure time per point (s) |
| `NONLOCAL_PRECONDITION_RMS_MA` | 0.1 | pre-condition current (mA RMS) |
| `NONLOCAL_PRECONDITION_S` | 30.0 | pre-condition duration (s) |
| `NONLOCAL_COMPLIANCE_V` | 50.0 | 6221 compliance (V) |
| `NONLOCAL_HARMONIC` | 2 | default harmonic |
| `NONLOCAL_MARKER_LINE` | 4 | Trigger Link line (1-6) |
| `NONLOCAL_MARKER_PHASE_DEG` | 0.0 | phase marker phase (°) |
| `NONLOCAL_MAX_PEAK_A` | 0.105 | maximum peak current (A) |
| `NONLOCAL_MAX_POINTS` | 10000 | maximum number of data points |
| `NONLOCAL_SR830_FULL_SCALE_V` | 28 ranges | sensitivity list from 2 nV to 1 V |

### 3.3 F tab defaults

| Constant | Default | Meaning |
|---|---|---|
| `FREQUENCY_SWEEP_CURRENT_RMS_MA` | 0.5 | fixed current (mA RMS) |
| `FREQUENCY_SWEEP_START_HZ` | 7 | sweep start (Hz) |
| `FREQUENCY_SWEEP_STOP_HZ` | 1000 | sweep stop (Hz) |
| `FREQUENCY_SWEEP_SAMPLE_HZ` | 1.0 | sample rate (Hz) |
| `FREQUENCY_SWEEP_SETTLE_S` | 10.0 | settle time (s) |
| `FREQUENCY_SWEEP_MEASURE_S` | 100.0 | measure time (s) |
| `FREQUENCY_SWEEP_PRECONDITION_S` | 30.0 | pre-condition duration (s) |

### 3.4 VI tab defaults

| Constant | Default | Meaning |
|---|---|---|
| `VI_MAX_ABS_PULSE_V` | 20.0 | software limit of the pulse voltage (V) |
| `VI_START_PULSE_V` | 20.0 | pulse start (V) |
| `VI_STOP_PULSE_V` | -20.0 | pulse stop (V) |
| `VI_STEP_PULSE_V` | 4.0 | pulse step (V) |
| `VI_PULSE_WIDTH_S` | 0.1 | pulse width (s) |
| `VI_POST_PULSE_SETTLE_S` | 0.5 | post-pulse wait (s) |
| `VI_2400_CURRENT_COMPLIANCE_A` | 1e-3 | 2400 current limit (A) |
| `VI_READ_CURRENT_RMS_MA` | 1.0 | 6221 probe current (mA RMS) |
| `VI_FREQUENCY_HZ` | 13.7 | 6221 frequency (Hz) |
| `VI_SAMPLE_HZ` | 1.0 | SR830 sample rate (Hz) |
| `VI_6221_SETTLE_S` | 10.0 | 6221 settle time (s) |
| `VI_MEASURE_S` | 100.0 | measure time (s) |
| `VI_6221_COMPLIANCE_V` | 50.0 | 6221 compliance (V) |
| `VI_MIN_RESISTANCE_FOR_PULSE_OHM` | 20e6 | resistance gate (Ω) |
| `VI_RES_CURRENT_LEVELS_A` | 13 levels | 1 nA – 1 mA adaptive current list |
| `VI_RES_TARGET_LOW_V` | 0.05 | lower target voltage (V) |
| `VI_RES_TARGET_HIGH_V` | 2.0 | upper target voltage (V) |
| `VI_RES_COMPLIANCE_V` | 5.0 | 2400 voltage limit (V) |
| `VI_RES_SETTLE_S` | 0.2 | resistance settle time (s) |
| `VI_RES_AVERAGE_COUNT` | 5 | resistance averaging count |
| `VI_RES_NPLC` | 1.0 | resistance NPLC |

---

## 4. Key SCPI command reference

### 4.1 Keithley 6221

| Command | Meaning |
|---|---|
| `CURR:COMP {V}` | set the compliance |
| `SOUR:WAVE:FUNC SIN` | select sine wave |
| `SOUR:WAVE:FREQ {Hz}` | set frequency |
| `SOUR:WAVE:AMPL {A}` | set peak current (A) |
| `SOUR:WAVE:OFFS 0` | zero offset |
| `SOUR:WAVE:RANG BEST` | autorange |
| `SOUR:WAVE:PMAR {deg}` | phase marker phase |
| `SOUR:WAVE:PMAR:OLIN {n}` | Trigger Link output line (1-6) |
| `SOUR:WAVE:PMAR:STAT ON` | enable the phase marker |
| `SOUR:WAVE:ABOR` | abort the waveform |
| `CURR 0` | zero the current |
| `OUTP OFF` | output off |
| `SYST:ERR?` | query the error queue |
| `OUTP?` | query the output state |
| `SOUR:WAVE:ARM` | arm the waveform |
| `SOUR:WAVE:INIT` | start the waveform |
| `STAT:MEAS:COND?` | query the measurement condition |

### 4.2 Keithley 2400

| Command | Meaning |
|---|---|
| `*RST` / `*CLS` | reset / clear events |
| `SOUR:FUNC VOLT` | voltage source mode |
| `SOUR:FUNC CURR` | current source mode |
| `SOUR:VOLT {V}` / `SOUR:CURR {A}` | set the output value |
| `SOUR:VOLT:MODE FIXED` / `SOUR:CURR:MODE FIXED` | fixed mode |
| `SENS:CURR:PROT {A}` | current limit |
| `SENS:VOLT:PROT {V}` | voltage limit |
| `SENS:FUNC 'CURR'` / `'VOLT','CURR'` | measurement function |
| `SENS:VOLT:RANG:AUTO ON` / `SENS:CURR:RANG:AUTO ON` | autorange |
| `SENS:VOLT:NPLC {n}` | NPLC |
| `FORM:ELEM VOLT,CURR` | output elements |
| `READ?` | read a measurement |
| `SYST:ERR?` | error queue |
| `OUTP?` / `OUTP OFF` | output state / off |

### 4.3 Stanford Research SR830

| Command | Meaning |
|---|---|
| `FMOD 0` | external reference mode |
| `RSLP 2` | TTL falling-edge trigger |
| `HARM {n}` | harmonic |
| `ISRC 1` | external signal input |
| `IGND 0` | ground float |
| `ICPL {0/1}` | coupling (0=AC, 1=DC) |
| `ILIN 1` | line filter |
| `SENS {n}` | sensitivity level (0-27) |
| `OFLT 10` | time constant |
| `OFSL 1` | filter slope |
| `RMOD 1` | reserve mode |
| `PHAS 0` | phase 0° |
| `DDEF 1,1,0` | front-panel channel 1 display |
| `DDEF 2,1,0` | front-panel channel 2 display |
| `SNAP? 1,2,3,4,9` | fast read of X, Y, R, phase, ref_freq |
| `LIAS?` | lock-in status bits |
| `FREQ?` | current reference frequency |
| `*ESR?` | event status register |
| `*IDN?` | instrument identity |

---

## 5. Data output format

### 5.1 Raw CSV columns

**IV Tab / F Tab**：

| Column | Meaning |
|---|---|
| `index` | sample index |
| `data_quality` | data quality: `preconditioning` / `settling` / `formal` |
| `current_rms_ma` | actual 6221 RMS current |
| `frequency_hz` | actual 6221 frequency |
| `X_V` | SR830 X component |
| `Y_V` | SR830 Y component |
| `R_V` | SR830 R amplitude |
| `phase_deg` | phase (°) |
| `reference_frequency_hz` | SR830 reference frequency |
| `lia_status` | SR830 status bits |
| `status_text` | status text (OK/overload/unlock) |
| `reference_valid` | whether the reference is locked |
| `in_compliance` | whether within the compliance limit |
| `sensitivity_index` | current sensitivity level |
| `auto_range` | autorange action record |
| `timestamp` | timestamp |

**VI tab**: additionally includes pulse voltage, 6221 probe current, 2400 resistance columns, etc.

### 5.2 Summary CSV

- only samples with `data_quality == "formal"` and normal status
- mean, standard deviation and standard error are computed
- overloaded, unlocked and out-of-compliance samples are excluded

### 5.3 PNG plots

- live R–time plot (cumulative)
- live θ–time plot
- IV curve (R vs I)
- frequency sweep plot (R vs f)
- prime-frequency markers

---

## 6. Thread-safety model

### 6.1 Cross-tab exclusion

- each tab uses its own `ThreadPoolExecutor(max_workers=1)` and `threading.Event`
- the VI tab uses the `VIMeasurementInterlock` state machine so the 2400 and SR830 are never accessed at the same time
- instrument controllers are protected internally by `threading.RLock()`

### 6.2 UI thread safety

- measurement threads send UI callbacks through a `queue.Queue`
- the main thread drains the queue every 40 ms via `root.after()` (`_drain_ui_queue`)
- all Tkinter operations run on the main thread

### 6.3 Data locks

- each tab protects its data lists (`time_rows`, `summary_rows`) with a `threading.Lock()`
- prevents write/read races

---

## 7. Safety mechanisms

### 7.1 All exit paths

| Trigger | Clean-up |
|---|---|
| Stop button | set `stop_event` → raise `VIMeasurementStopped` → `finally` clean-up |
| window close | `WM_DELETE_WINDOW` → `request_close()` → safe shutdown of all instruments |
| Ctrl+C (terminal) | `KeyboardInterrupt` → `finally` clean-up |
| exception | `except` → `finally` clean-up |

### 7.2 Safe instrument shutdown

**6221**：
```
SOUR:WAVE:ABOR → CURR 0 → OUTP OFF
```

**2400**：
```
SOUR:VOLT 0 → SOUR:CURR 0 → OUTP OFF
(or)
SOUR:CURR 0 → OUTP OFF
```

### 7.3 Output confirmation

- `confirm_output_off()` is called after every safe shutdown to verify the physical output is really off
- if the check fails a forced off is attempted; if that also fails an exception blocks further operations

---

## 8. Known issues and notes

### 8.1 Hard-coded addresses

- all VISA addresses are hard-coded at the top of the file; change them manually after moving PC/GPIB controller
- verify the addresses in NI MAX before running

### 8.2 No simulated tests

- the script cannot run or be tested without real instruments
- no mock instruments, no unit tests

### 8.3 Many hard-coded constants

- the top ~150 lines hold every default; consider moving them to a configuration file
- the 4 tabs each carry their own set of UI variables, which is costly to maintain

### 8.4 Instrument readback verification

- 6221 frequency readback tolerance: `rel_tol=5e-3, abs_tol=0.1`
- SR830 reference frequency readback tolerance: the same
- a large frequency offset on the real instrument makes configuration fail

### 8.5 SR830 misaligned replies

- a failed query is retried twice, flushing the buffer and waiting 80 ms each time
- 3 consecutive failures still raise

### 8.6 Prime-frequency sweep

- the prime list is generated in code (integer primes between `FREQUENCY_SWEEP_START_HZ` and `STOP_HZ`)
- default range 7–1000 Hz

### 8.7 Resistance gate

- on the VI tab the next pulse is allowed only when `|R| < 20 MΩ`
- voltage points blocked by the gate stay in the data but are flagged as blocked

---

## 9. Usage guide

### 9.1 Pre-start checks

1. confirm the GPIB connection and verify the VISA addresses in NI MAX
2. confirm `pyvisa` and `pyvisa-py` are installed: `py -m pip install pyvisa pyvisa-py`
3. confirm matplotlib has a font with CJK support (the script auto-detects)

### 9.2 Starting

```bash
python NonLocal_NH_dev.py
```

### 9.3 Per-tab workflow

**IV Tab**：
1. enter the 6221 address and select the SR830s (#8 / #9 / both)
2. click "Connect selected"
3. set the current range (start/stop/step)
4. set frequency, sample rate, settle time and measure time
5. set the pre-condition parameters (optional)
6. click "Start measurement"

**F Tab**：
1. connect the instruments (as above)
2. set the fixed current and the frequency range (start/stop, primes)
3. set sample rate, settle time and measure time
4. click "Start measurement"

**R Tab**：
1. enter the 2400 address
2. click "Connect"
3. measurement starts automatically and the resistance is shown live

**VI Tab**：
1. connect the 2400 and the 6221/SR830
2. set the pulse voltage range (start/stop/step)
3. set the pulse width and post-pulse wait
4. set the 6221 probe current and frequency
5. set the resistance gate (MΩ)
6. click "Start measurement"

### 9.4 Common problems

| Problem | Likely cause | Fix |
|---|---|---|
| connection fails | wrong GPIB address / instrument off | check the address in NI MAX, confirm power |
| 6221 frequency readback mismatch | instrument frequency offset / tolerance too tight | check the instrument settings, or ask the author to adjust the tolerance |
| SR830 misaligned reply | GPIB bus contention / timing | the script retries; if it still fails, check the wiring |
| 2400 error | instrument in an error state | check the error queue, try restarting the instrument |
| resistance above the gate | sample resistance too high / bad contact | check the sample wiring, confirm the gate value is sensible |

---

## 10. File structure summary

```
NonLocal_NH_dev.py  (4808 lines)
├── module docstring (L1-13)
├── imports (L15-35)
├── hard-coded constants (L37-150)
│   ├── VISA resource addresses
│   ├── IV tab parameters
│   ├── F tab parameters
│   └── VI tab parameters
├── VIMeasurementStopped (L152-156)
├── VIMeasurementInterlock (L158-185)
├── Keithley2400VIController (L187-467)
│   ├── connect / disconnect
│   ├── setup_voltage_mode
│   ├── apply_voltage_pulse
│   ├── _setup_resistance_mode
│   ├── measure_resistance (adaptive)
│   └── initialize_continuous_resistance
├── NonLocalACController (L469-969)
│   ├── connect / disconnect
│   ├── configure (6221 + SR830)
│   ├── set_current
│   ├── wait_references
│   ├── read_samples (SNAP + LIAS)
│   ├── _auto_range
│   └── output_off
├── NonLocalIVApp (L971-4790)
│   ├── __init__ (tab initialisation + UI variables)
│   ├── _build_ui (builds the 4 tabs)
│   ├── _build_iv_ui / _build_frequency_ui
│   ├── _build_vi_ui / _build_resistance_ui
│   ├── per-tab measurement workers
│   ├── live plotting
│   ├── data saving (CSV + PNG)
│   └── safe shutdown
└── main() (L4792-4808)
```

---

## 11. Relation to other project scripts

- `NonLocal_NH_dev.py` is **fully standalone** and depends on no other `.py` file
- no dependency on `MOKE_NH_dev.py` (stated explicitly in the docstring)
- similar to `PPMS1/6221_SR830_2400_VI_sweep.py` but more complete (4 tabs vs 2)
- shares the instrument configuration (6221 + dual SR830) with `MOKE/6221_dual_SR830_IV_sweep.py`
- corresponds to the experimental data in `PPMS2/B3A/`

---

## 12. Dependencies

| Package | Purpose |
|---|---|
| `pyvisa` / `pyvisa-py` | GPIB instrument communication |
| `tkinter` | GUI framework |
| `matplotlib` | live plots + PNG export |
| `concurrent.futures` | thread pools |
| `queue` | thread-safe UI queue |
| `statistics` | mean / standard deviation |
| `csv` | data saving |
| `math` | maths |
| `threading` | thread safety |
| `time` | timing / waits |
| `datetime` | timestamps |
| `pathlib` | path handling |

**Python requirement**: Python 3.10+ (uses the `X | Y` union syntax and `match` statements)

---

*This document was generated from a complete analysis of the 4808-line `NonLocal_NH_dev.py`. When in doubt, consult the source.*
