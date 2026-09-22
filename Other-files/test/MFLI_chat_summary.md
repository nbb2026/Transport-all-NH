# Zurich Instruments MFLI usage and connection notes

> This document summarises the full discussion about the Zurich Instruments MFLI lock-in amplifier: device identification, Python connection, troubleshooting, front and rear panel connectors, LabOne parameters, and how to handle the input-overload red LED.

## 1. Basic device information

The lab instrument is a Zurich Instruments MFLI lock-in amplifier.

- Model: MFLI
- Serial / API device name: `MF-DEV4435` / `dev4435`
- MAC address: `80:2F:DE:00:11:53`
- Instrument IP: `192.168.69.78`
- LabOne Data Server port: `8004`
- Observed LabOne version: `18.12.59443`
- Discovery interface: `PCIe` (the interface between the instrument's embedded system and its measurement hardware, not a PCIe card in the PC)
- PC address used on the USB virtual network: `192.168.69.77`
- Calibration label: `Next Calibration Aug 2021`

The rear panel offers both USB Device and LAN control paths. The instrument has been connected successfully over the virtual network created by USB Device.

## 2. Final Python connection result

### 2.1 Working Python environment

The interpreter that ran the script successfully:

```powershell
C:\ProgramData\miniconda3\envs\GMS_VENV_PYTHON\python.exe
```

Successful command:

```powershell
& C:\ProgramData\miniconda3\envs\GMS_VENV_PYTHON\python.exe D:\codex\mfli_connect.py
```

Successful output included:

```text
Discovered device `dev4435`: MFLI
Creating an API session ... apilevel `5`
Connected device : dev4435
Demodulator      : 0
Reference freq.  : 100000 Hz
X                : ... V
Y                : ... V
R = sqrt(X^2+Y^2): ... V
Theta            : ... deg
Samples received : 10
```

This proves that all of the following work:

1. Python can import `zhinst`;
2. LabOne Discovery finds `dev4435`;
3. Python can open an API level 5 session;
4. Data Server communication works;
5. demodulator samples can be read;
6. X, Y, R and phase data are returned.

### 2.2 Connection chain

The actual chain of the successful connection:

```text
Python script
    ↓
LabOne API / TCP 8004
    ↓
Windows RNDIS virtual network adapter
    ↓
USB Device cable
    ↓
MFLI DEV4435（192.168.69.78）
```

### 2.3 Python environment error

Initially used:

```powershell
D:\python\python.exe
```

Result:

```text
ModuleNotFoundError: No module named 'zhinst'
```

The cause was that the Zurich Instruments Python API was not installed in that Python environment.

The same error appeared later after switching to another Conda environment, showing that "changing Python environment" does not bring packages from other environments along. Every Python/Conda environment has its own `site-packages`.

Always install or check packages with the interpreter that actually runs the script, e.g.:

```powershell
& C:\ProgramData\miniconda3\envs\GMS_VENV_PYTHON\python.exe -m pip show zhinst
```

### 2.4 LabOne / zhinst version compatibility

An error like this appeared:

```text
Unable to create the LabOne API session.
Use the CPython 3.7 environment containing zhinst==18.12.59443.
```

Root cause:

- the instrument runs the old LabOne `18.12.59443`;
- the new `zhinst` API has interface/parameter incompatibilities with the old Data Server;
- an old `zhinst` matching LabOne 18.12 and a compatible Python version are required.

The part of the error reading:

```text
required_devtype is not supported anymore and will be removed in the future
```

is essentially an API generation mismatch, not a sign that the MFLI model itself is unsupported.

### 2.5 Network reachable but API failing

Executed:

```powershell
Test-NetConnection 192.168.69.78 -Port 8004
```

Result:

```text
TcpTestSucceeded : True
```

A Python socket test also gave:

```text
Python TCP OK
```

But the old script still reported:

```text
Failed to connect to the data server 192.168.69.78:8004
```

This shows:

- IP, routing, firewall and TCP port are actually fine;
- the failure is in the LabOne API protocol/version handshake, not the network layer;
- changing the Windows firewall alone cannot fix an API version mismatch.

## 3. Front panel connectors

## 3.1 Signal Input I

Current input for measuring AC or DC current directly.

- Front-panel maximum: `<10 mApk`
- a transimpedance amplifier converts the current to a voltage internally;
- suitable for photodiodes, current-output sensors, impedance measurement, etc.;
- choose the Current Input Range from the maximum current;
- too small a range saturates the current-input ADC and lights the matching red LED.

## 3.2 Signal Input +V

Main voltage input.

- single-ended mode: measures `+V` against the input shield/ground;
- differential mode: the positive side of the differential input;
- input range, AC/DC coupling, 50 Ω/10 MΩ, Diff and Float are all set in LabOne.

## 3.3 Signal Input −V Diff

Negative side of the differential voltage input.

- with `Diff = ON` it takes part in the measurement:

  ```text
  Vin = V(+V) - V(-V Diff)
  ```

- with `Diff = OFF` the instrument grounds it internally; do not treat it as a second independent input channel.

## 3.4 Signal Output +V

Main analogue output: the internal oscillator's sine wave, a DC offset and multi-frequency composite signals.

- source impedance about 50 Ω;
- front panel marked `<10 Vpk`;
- the output range is selected in LabOne;
- the total output includes the sine components, Offset and possibly an added Aux Input;
- a total peak beyond Range clips and raises OVO.

## 3.5 Signal Output −V Diff

Negative side of the differential output.

- `Diff = OFF`: not used as an independent output;
- `Diff = ON`: outputs a waveform equal in amplitude and opposite in phase to `+V`;
- a differential load sees the difference between the two.

## 3.6 Aux Input 1/2 Ref

Two auxiliary analogue inputs, also usable as external reference inputs.

- input impedance about 1 MΩ;
- front panel marked `<10 Vpk`;
- can acquire slow control quantities, sensor voltages, position feedback, etc.;
- can be selected by a demodulator as its input or external reference source.

## 3.7 Aux Output 1～4

Four auxiliary analogue outputs.

- output range typically about ±10 V;
- can output demodulator X, Y, R, phase, PID output, fixed values, etc.;
- suitable for scanners, feedback controllers or external recorders;
- they are not four extra high-speed main sine outputs.

## 3.8 Front-panel indicators

- red LED next to the current input: Current Input Signal Over;
- red LED next to the voltage input: Voltage Input Signal Over;
- indicator next to Signal Output: output state/fault;
- rightmost blue LED: power/run state.

## 4. Rear panel connectors

## 4.1 AC mains inlet

- input: 100–240 VAC;
- frequency: 50/60 Hz;
- maximum power marked about 60 W;
- a three-core cable with protective earth is mandatory;
- never remove the protective earth to break a ground loop.

## 4.2 Power switch and fuse

- `I`: on;
- `O`: off;
- fuse marked `F2AL250V`;
- unplug the mains before replacing the fuse and use exactly the same rating;
- a repeatedly blowing fuse indicates a possible fault; never bridge the fuse.

## 4.3 DC In

Auxiliary external DC power input. This unit is marked `12 V / 2 A`, while newer Zurich documentation for current hardware mentions 18 V.

Because DEV4435 is older hardware running LabOne 18.12:

- use only the DC adapter originally supplied with this unit;
- without the original adapter, give the serial number to Zurich for confirmation;
- do not apply 18 V based on the newer web documentation;
- do not assume the AC and DC inputs can be used together as redundant supplies.

## 4.4 DIO 32 bit

68-pin VHDCI digital input/output connector.

- logic level: 3.3 V TTL/LVCMOS;
- 32 bits usually in four groups of 8;
- exchanges digital states with FPGAs, DAQs or custom controllers;
- usable for hardware synchronisation, triggering, scan state and handshaking;
- must not be connected directly to 5 V TTL, 12 V or 24 V PLC signals;
- use level translation or digital isolation where needed.

## 4.5 USB 2.0 Host ×2

Two USB-A host ports.

- for USB sticks, supported wireless adapters, upgrade media, etc.;
- the MFLI is the USB host here;
- do not connect a PC with a USB-A to USB-A cable.

## 4.6 USB 2.0 Device

Square USB-B port for connecting a PC.

- creates an RNDIS virtual network adapter on the PC;
- the current working link `192.168.69.77 ↔ 192.168.69.78` comes from this port;
- carries the LabOne UI, Data Server and Python API data;
- USB ties PC ground to instrument ground and may introduce ground-loop noise in weak-signal measurements.

## 4.7 LAN 1 GbE

Gigabit Ethernet port.

- connects directly to a PC or to the LAN;
- gives access to LabOne and the Data Server;
- with ordinary UTP Cat5e/Cat6 cable it usually reduces grounding noise compared with USB;
- when USB and LAN coexist, the program should specify the target IP explicitly so discovery does not pick the wrong interface.

## 4.8 Trigger In 1/2

Two rear-panel hardware trigger inputs.

- input impedance about 1 kΩ;
- normal input range marked about ±5 V;
- accepts TTL-type pulses, camera exposure, chopper, laser sync, function-generator Sync, etc.;
- can trigger Scope/DAQ or serve as an external reference;
- Trigger In is not a 50 Ω input and must not be driven directly with 12 V or 24 V trigger signals.

## 4.9 Trigger Out 1/2

Two digital trigger outputs.

- source impedance about 50 Ω;
- output about 5 V digital pulses;
- can output oscillator phase, DAQ/Scope state, threshold comparison results, etc.;
- they are not analogue X/Y outputs;
- two outputs, or the outputs of two instruments, must not be paralleled directly.

## 4.10 Clock 10 MHz In

External 10 MHz system clock input.

- unifies the time base of several instruments;
- not an ordinary experiment reference input;
- after wiring, set Clock Source to External on the Device page.

## 4.11 Clock 10 MHz Out

Outputs the MFLI's 10 MHz system clock to other instruments.

- lets oscilloscopes, signal sources or other lock-ins follow the same reference;
- must not be connected directly to another 10 MHz output;
- use a dedicated clock distributor for several devices.

## 4.12 Chassis ground terminal

4 mm banana socket connected to the chassis and protective earth.

- for chassis equipotential bonding, shielding and EMI control;
- not an ordinary signal input;
- no substitute for the mains protective earth;
- too many ground paths can form ground loops.

## 5. LabOne Lock-in tab parameters

The example screenshots discussed were of `DEV4414`, which has the MF-MD multi-demodulator option and therefore shows 4 oscillators and 4 demodulators. The parameter meanings are the same for DEV4435, but the specific configuration in the screenshots does not necessarily apply to DEV4435.

## 5.1 Signal Inputs

### Voltage Input Range

Controls the analogue front-end range of the voltage input. The range must cover the full raw waveform, including AC peak, DC offset, noise and transients.

Recommendation:

```text
Range ≈ 1.5–2 × the maximum input peak
```

Too small a range overloads; too large a range wastes ADC dynamic range.

### Auto Range

Measures the current input automatically and picks a suitable range. Useful on first wiring and when the amplitude is unknown.

### Scaling and Measurement Unit

Only changes the displayed conversion, not the analogue gain.

For example with an external amplifier gain of 100, set Scaling to 0.01 to display the signal before amplification.

### AC

- OFF: DC coupling;
- ON: AC coupling with a high-pass filter to suppress DC offset;
- MFLI AC-coupling cut-off about 1.6 Hz;
- very-low-frequency measurements should normally keep AC coupling off.

### 50 Ω

- OFF: about 10 MΩ high-impedance input;
- ON: 50 Ω termination;
- with a 50 Ω source into a 50 Ω input, the received voltage may be half the open-circuit voltage.

### Diff

- OFF: single-ended measurement;
- ON: differential measurement `V(+V) - V(-V)`.

### Float

- OFF: input shield tied to instrument ground;
- ON: input shield floating;
- floating is not high-voltage isolation; the allowed common-mode/float voltage still applies.

## 5.2 Oscillators

Digital oscillators generate the sine/cosine references for demodulation and output.

- `Manual`: user-set frequency;
- `ExtRef`: the oscillator tracks an external reference via PLL;
- green dot: the external reference is locked.

In the example screenshot:

```text
Oscillator 1 = ExtRef ≈ 317.29538186 Hz
```

## 5.3 Demodulators

Each demodulator has:

- Reference Mode: normal measurement or external-reference recovery;
- Osc: which oscillator to use;
- Harm: harmonic multiplier;
- Demod Freq: the actual demodulation frequency;
- Phase: reference phase compensation;
- Input Signal: the input source analysed.

Frequency relation:

```text
Demod Freq = Oscillator Frequency × Harmonic
```

Example:

```text
317.29538186 Hz × 2 = 634.59076372 Hz
```

`Zero Phase` adjusts the reference phase so that X is maximal, Y near 0 and phase near 0°, while R is unchanged. Do not use it casually when measuring absolute phase.

## 5.4 Low-Pass Filters

The low-pass filter suppresses high-frequency components and noise after mixing.

Filter order:

| Order | Roll-off |
|---:|---:|
| 1 | 6 dB/oct |
| 2 | 12 dB/oct |
| 3 | 18 dB/oct |
| 4 | 24 dB/oct |
| 5 | 30 dB/oct |
| 6 | 36 dB/oct |
| 7 | 42 dB/oct |
| 8 | 48 dB/oct |

The filter can be shown as:

- `BW 3 dB`: −3 dB bandwidth;
- `BW NEP`: noise-equivalent bandwidth;
- `TC`: time constant.

Rule of thumb:

- small bandwidth: low noise, slow response;
- large bandwidth: fast response, high noise.

The `Sinc` extra filter is mainly for very low demodulation frequencies; it is usually off for high-frequency measurements.

## 5.5 Data Transfer

### Enable

Controls whether this demodulator streams samples to the Data Server/PC continuously.

### Rate (Sa/s)

The number of demodulated samples sent to the PC per second; not the ADC's 60 MSa/s raw rate, and it does not change the low-pass bandwidth.

The transfer Rate can generally be set to about 7–10 × the filter bandwidth. Setting it higher only loads the network, PC and files and does not raise the analogue measurement bandwidth.

## 5.6 Trigger and Trig Mode

- `Continuous`: streams at the set Rate;
- `Trigger 1/2`: sends samples according to the rear-panel trigger inputs;
- `Rising/Falling/Both`: rising, falling or both edges;
- `High/Low`: streams continuously only during the matching level.

Data Transfer Trigger and "using Trigger 1 as the external reference signal" are two different functions.

## 5.7 Output Amplitudes

MF-MD allows several sine components to be combined into the same Signal Output.

- `Mode = Sine`: sine wave;
- `Amp 1 (Vpk)`: peak voltage added to Output 1;
- the switch on each row enables that frequency component.

The total output is:

```text
Vout = Offset + sum of sine components + Aux Input Add
```

Two components with the same frequency and phase add coherently; e.g. two 100 mVpk components may combine to 200 mVpk.

## 5.8 Signal Outputs

- `On`: physical output master switch;
- `50 Ω`: tells LabOne whether the load is 50 Ω, for correct display of the load voltage;
- `Range`: total allowed output range;
- `Offset`: DC offset;
- `Add`: adds Aux Input 1 to the main output;
- `Diff`: enables the differential output.

## 5.9 Numeric display

Lock-in results:

```text
X = R cos(θ)
Y = R sin(θ)
R = sqrt(X² + Y²)
θ = atan2(Y, X)
```

- X: in-phase component;
- Y: quadrature component;
- R: RMS amplitude;
- Phase: phase relative to the reference.

Numeric presets:

- `Demods Polar`: R and Phase of all demodulators;
- `Enabled Demods Polar`: R and Phase of enabled demodulators;
- `Demods Cartesian`: X and Y of all demodulators;
- `Enabled Demods Cartesian`: X and Y of enabled demodulators;
- `Demods R`: R only;
- `Unpopulated`: cleared;
- `Manual`: manually chosen display nodes.

Lin, Log, dB, FS, Manual and Auto only change the display scaling, not the measurement.

## 5.10 Bottom status bar

- `REC`: background data recording;
- `MDS`: multi-device synchronisation;
- `CF`: external 10 MHz clock fault;
- `OVI`: input overload;
- `OVO`: output overload/clipping;
- `COM`: sample loss, packet loss or stalled transfer;
- `API Log`: shows the API commands matching UI actions.

LabOne UI numbering usually starts at 1 while API node indices start at 0, e.g.:

```text
UI Demod 1 → /dev4435/demods/0/
UI Demod 2 → /dev4435/demods/1/
```

## 6. Why the voltage-input red LED stays on and what to do

The LED that stayed on in the photo is behind the Voltage Signal Input and is:

```text
Voltage Input Signal Over
```

It means the voltage-input analogue front end or ADC is saturated; amplitude and phase results may then be unreliable.

### 6.1 Why it overloads although Numeric shows only tens of microvolts

R in Numeric only represents the narrow-band, low-pass-filtered component at one demodulation frequency. The LED watches the complete raw waveform entering the ADC, including:

- the target signal;
- other frequencies;
- DC offset;
- 50 Hz interference;
- transient spikes;
- common-mode voltage on a differential input.

Example:

```text
target 634.6 Hz component: 70 µV
DC offset: 50 mV
input Range: 10 mV
```

The lock-in result is only 70 µV, but the 50 mV DC already saturates the 10 mV range.

### 6.2 Recommended procedure

1. Open `Lock-in → Signal Inputs → Voltage Input 1`;
2. click Auto Range;
3. or raise the Range manually in this order:

   ```text
   10 mV → 100 mV → 1 V → 3 V
   ```

4. inspect the raw waveform of Signal Input 1 with the Scope;
5. check DC offset, peak, Vpp/Vpk/Vrms and spikes;
6. confirm the single-ended/differential mode matches the wiring;
7. check the source's actual output into high-impedance and 50 Ω loads;
8. if only AC components above a few Hz are needed and a large DC is present, consider AC coupling, but AC coupling is no substitute for overvoltage protection;
9. make sure `OVI` at the bottom finally returns to grey.

### 6.3 Quick isolation

1. set the Voltage Input Range to 3 V;
2. switch the external signal source off;
3. watch the red LED;
4. if still on, unplug the `+V` and `−V Diff` input cables;
5. watch the LED again and check with the Scope;
6. if the LED stays on with no input at the 3 V range, restart and load the Factory preset;
7. if it still does not recover, contact Zurich for a hardware check.

Low-pass bandwidth, filter order, harmonic and Data Transfer Rate cannot remove an input overload, because the overload happens before the demodulator.

## 7. Recommendations for further use

1. Always run MFLI scripts with the Conda interpreter that was verified to work.
2. Do not upgrade the `zhinst` package on its own unless LabOne/firmware compatibility is planned at the same time.
3. At each experiment first confirm the raw waveform with a large range and the Scope, then reduce the Range.
4. Make sure `OVI`, `OVO`, `CF` and `COM` show no red alarm during the experiment.
5. For long acquisitions prefer the Python API with timestamps; do not rely on the browser display alone.
6. If nanovolt or microvolt measurements suffer from USB ground loops, consider LAN over UTP cable.
7. Keep a verified LabOne preset and the matching Python environment details so experiments stay reproducible.
8. DEV4435's calibration date is past the label's recommendation; schedule recalibration if traceable absolute amplitude, phase or frequency accuracy is required.

## 8. Related local files

- Python connection script: `D:\codex\mfli_connect.py`
- This summary: `D:\codex\MFLI_chat_summary.md`

