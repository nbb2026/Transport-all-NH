# MOKE combined control program handoff notes

> Folder note: this document was moved from `MOKE/` to `docs/`. Historical file names without a folder prefix
> still refer to the original `MOKE/` location; for the current entry point and actual paths see
> [`../README.md`](../README.md).

> Last updated: 2026-08-27 (Asia/Singapore)  
> Current development entry: `../Magneto_Electro_Optical_Microwave_Transport_NH_dev.py`  
> Current Windows build-package revision: `V3 / 3.2.0 PY3119 NUITKA413 MINGW64 PCOFIX ROT90 SCANLOG`  
> Status: the V3 build package was frozen from the current dev source; the dev source passes 27 and the release source
> 28 offline regression tests;
> awaiting the Windows Nuitka build, the post-build PCO EXE self-test and per-module hardware acceptance.
> V1 `1.0.1 PCOFIX` remains the historical baseline that compiled successfully.

> V3 already includes the counter-clockwise 90° orientation normalisation of the whole PCO pipeline, the physical corner
> mapping top-left/bottom-left/bottom-right/top-right `4/1/2/3`, the MOKE scan/export hardware-state log, and the scan-start
> snapshot timing fix. At freeze time the release source matched dev except for the release identity and the PCO self-test file name.

> Dev differences: after the V3 3.2.0 freeze, the active source extended the 6221 pulse current–ROI feature:
> both the current round-trip and the fixed-current multi-pulse flows average the difference ROI over the set interval after each pulse,
> the right-hand plot uses current or pulse number as x-axis, and the MOKE raw uint16 TIFF at the end of the acquisition window can be
> saved; the left-hand plot became the same live ROI–time as MOKE. The MOKE active path also gained a gaussmeter closed-loop field
> zeroing with a small probe-current slope calibration and step-limited iteration, returning to 0 A on error. The dev build
> also fixed the status judgement of microsecond-scale finite 6221 pulses: by the official definition, a finite duration expiring
> switches the output off automatically, so when cycles/waveform parameters read back correctly and the SCPI error queue is empty, an
> immediately following `OUTP=0` is logged as "completed before query" instead of a false start failure; a cycles mismatch still shuts down safely and raises.
> Standalone pulses also write `WAVE:EXTR OFF` explicitly before ARM, so a stale external-trigger state cannot leave the
> instrument waiting for Trigger Link.
> 34 offline tests pass; these changes are not yet in the V3 ZIP.

This document records the Windows single-file release process of the current MOKE control program, the build and
runtime problems met so far, and the verification gates the next maintainer must keep. Program features and hardware mapping are in
`README.md`; this document focuses on reproducible builds and releases.

## V3 current single-file build package (2026-08-27)

- Development source: `../Magneto_Electro_Optical_Microwave_Transport_NH_dev.py`
- Build ZIP: `../MEO_Transport_V3_Windows_Build_Package.zip`
- ZIP SHA-256：
  `129064e2db5dffdac41a2ac3ba40cfef11d096ea0625ad9cf8c8e775ef4abce4`
- Build package folder: `../MEO_Transport_V3_Windows_Build_Package/`
- Build entry: `BUILD_AND_INSTALL_V3.bat`
- Frozen source: `Magneto_Electro_Optical_Transport_V3.py`
- Frozen source SHA-256:
  `b90f84e60e284e24ed724b869769e172b85b18b60b679d39bea0d569159b7033`
- Build baseline: CPython 3.11.9 x64, Nuitka 4.1.3, MinGW64, one-file.
- Release output: `V3_Compiled_Output` collects the verified EXE, its SHA-256, the PCO self-test log
  and the Nuitka compilation report, and creates a shortcut pointing directly at that EXE
  `Magneto-Electro-Optical Transport V3.lnk`。
- Release gates: 27 dev and 28 release offline tests, the PCO source DLL inventory, the Nuitka
  payload DLL inventory, the post-build
  `--pco-dll-self-test`, a byte-for-byte comparison of the build and release EXE, and the EXE SHA-256.
- Display identity: the main window and Camera Live titles end with `-NH & Prof Chen's group`; the EXE uses
  the new Transport icon (magnetic field, light beam and four-terminal device) without a V3 graphic mark.
- Status boundary: macOS offline checks pass; the Windows build, PCO self-test and real-hardware acceptance have not been run.

Normal use needs only the final EXE, not the `.py`, a virtual environment or pip packages; NI-VISA, the PCO
SDK/driver, Kinesis, LabOne, MultiVu/QDInstrument and other vendor runtimes remain external prerequisites.
The V2 material below is kept as the historical build baseline and does not mean V3 has passed Windows acceptance.

### Zurich MFLI / LabOne 18.12 compatibility environment

The lab's DEV4435 was verified in a browser at `192.168.69.78` to show Demod 1 R and
Phase, and TCP 8004 is reachable. The root cause of the connection failure is that the instrument's LabOne `18.12.59443` does not match
the new zhinst API for Python 3.11; that PyPI version only ships CPython 2.7, 3.5,
3.6 and 3.7 wheels, with no CPython 3.11 wheel.

The source folder gained `INSTALL_MFLI_LABONE_18_12.bat`. It silently installs an isolated Python 3.7.9 under
`%LOCALAPPDATA%\NBB\MOKE_MFLI_PY37`, pins
NumPy 1.21.6, downloads and verifies the official `zhinst-18.12.59443-cp37-cp37m-win_amd64.whl` (SHA-256
`f92e32a5766308d2c62311eaf2c8205ca76ed304f6c0511443f4e326ef203040`), then sets
`MOKE_MFLI_PYTHON`. The main program checks that the Python is 3.7 and zhinst exactly
18.12.59443, and after verification prefers the JSON-line subprocess bridge; the main GUI and other hardware modules
stay on Python 3.11. Future Windows build packages must ship this BAT together with
`README_MFLI_LABONE_18_12_INSTALL.txt`。

## 1. Current release material

- Development source: `MOKE_NH_dev.py`
- Standalone Non-local I–V module: `../2-Python-Transport/NonLocal_NH_dev.py`
- Current build package: `MOKE_NH_V2_Windows_Build_Package.zip`
- Build package folder: `MOKE_NH_V2_Windows_Build_Package/`
- Build package revision: `2.0.0 INSTRUMENT-AUDIT MFLI PCOFIX NOPASSWORD`
- Build package SHA-256:
  `6dd1c944ac604c873357b020935a78327a5bb353f05d115aaafa59cbd8faa83b`
- Main files in the ZIP:
  - `MOKE_NH_V2.py`
  - `BUILD_AND_INSTALL_V2.bat`
  - `requirements_build_v2.txt`
  - `pco.nuitka-package.config.yml`
  - `create_desktop_shortcut.ps1`
  - `README_LAB_BUILD_CN.md`
  - `INSTALL_MFLI_LABONE_18_12.bat`
  - `tests/test_release_logic.py`
  - `assets/MOKE_NH_V2.ico`

The V2 icon is emerald/gold optics with a hysteresis loop and a separate `V2` badge, clearly distinguishable from the V1 blue/pink icon
and desktop shortcut. The V2 source SHA-256 is
`577e79134836b0a5e592fad2781ee625586d5df5a43a148d2b198066df7f9ef0`。

The SHA-256 above verifies the build package only and is not the hash of the EXE finally produced on the lab PC. Every formal
release should also record the EXE SHA-256 printed at the end of the batch script.

### 1.1 Standalone Non-local I–V module

`../2-Python-Transport/NonLocal_NH_dev.py` extracts the complete Non-local AC
I–V subsystem from `MOKE_NH_dev.py` without depending on the combined program, camera, magnets, LEDs, motor or 2410 module. It connects
a Keithley 6221 on its own and selects SR830 #8, #9 or both; each SR830 has its own input
coupling, harmonic and X/Y/R record channel. It keeps the GPIB misaligned-reply recovery, external-reference check,
stepwise autorange, pre-condition/settle/formal phase separation, live plots, raw/summary CSV, remaining time,
Start Formal Recording, Stop and safe shutdown on error. It passed syntax, standalone import, dual-SR830
acquisition/CSV column alignment and the simulated test that the worker's `finally` switches the 6221 output off; hardware acceptance is still needed.

### 1.2 Combined-program Non-local VI module

`1.0.3` integrated the reference program's VI flow into the rightmost page of the combined program and switched to the lab's actual
Keithley 2410 (GPIB 24) instead of the reference source's 2400. The 2410 handles voltage pulses and
absolute-resistance gating, the 6221 supplies the AC read current, and SR830 #8/#9 can read singly or together. The 2410's
1 nA–1 mA resistance autorange, 5 V voltage protection, NPLC and 1 mA default current limit share constants with the original 2410 page;
the two pages and 6221/Non-local IV exclude each other. Scans save raw,
summary and resistance-gate CSVs, a parameter log and plot PNGs live. Syntax, standalone import and the simulated 2410
pulse/resistance/output-off flows passed; hardware acceptance at low voltage and current is still needed after the Windows build.

### 1.3 2026-08-14 instrument control logic review

`MOKE_NH_dev.py` was reviewed against the Keithley 2410/6221/2182A, Stanford SR830 and Zurich MFLI official manuals,
and the following revisions were frozen into the V2 build package:

- The 2410 voltage-scan software limit is back to the user-specified ±200 V, also written to the instrument as
  `SOUR:VOLT:PROT 200`; ≤20 V uses the 20 V source range, >20 V the 1000 V source range.
  Before a range change the output is forced to zero and off, and at scan start a fixed range is preselected for the whole path to avoid
  autorange transients mid-scan. Non-local VI uses a fixed 20 V OVP and 20 V source range; resistance measurement keeps
  5 V compliance。
- 6221 sine AC still converts the GUI RMS current to the peak the instrument needs,
  `Ipeak = sqrt(2) * Irms`; boundary checks and readback verification were added for frequency, peak, compliance, Trigger Link line, amplitude, offset,
  cycle count and output state. Standalone square pulses remain 0↔I at 50% duty.
- The Non-local IV/VI 6221 gained a real AC/DC output choice: AC uses the sine wave and Trigger Link;
  DC uses the fixed current source, disables the frequency entry and restricts DC voltage reading to the 2182A, so unreferenced
  SR830/MFLI values are not taken as valid lock-in results. Both modes read back the setpoints and check compliance.
- SR830 external reference stays on the falling edge, harmonic × fundamental is limited to ≤102 kHz; a low signal needs
  3 consecutive readings before ranging down. Transient readings during overload, compliance or an autorange switch are no longer
  included in the formal point average.
- The MFLI external-reference PLL locks the fundamental and the measurement demodulator applies the chosen harmonic; the base model's
  demodulation frequency is limited to ≤500 kHz. The 1 s time constant, 2nd-order filter and a sufficient data
  rate are written and read back explicitly; the reading during a firmware autorange switch is excluded from the average.
- The 2182A enables Autozero and Front Autozero explicitly, keeping per-channel autorange and NPLC 1;
  the GUI/log label it as DC voltage monitoring so it is not mistaken for an SR830/MFLI phase-sensitive AC amplitude.
- In-app instrument resource exclusion was added: the same GPIB address or MFLI cannot be opened by different tabs/modules at the same
  time; it must be disconnected in the original module first, so several controllers never send commands to one instrument.
- Added the offline regression `test/test_instrument_safety_logic.py`, covering 2410 OVP/range,
  safe high/low range switching and duplicate-resource rejection. Syntax compilation and the 3 tests pass; hardware acceptance must still start from low-voltage,
  low-current parameters. The main program and the legacy LabOne bridge source pass their syntax check and 7 offline tests.
- V2 also restores the compiled EXE's `--pco-dll-self-test` non-GUI entry; the release script installs only after
  SDK, Recorder, Convert and XCite all load successfully from the one-file payload and
  `RESULT: PASS` is written. The package's release tests total 8.

## 2. Verified Windows build baseline

The environment of the first successful single-file EXE build:

| Item | Verified version |
| --- | --- |
| Operating system | Windows 11 x64 |
| Python | CPython 3.11.9 x64 |
| Nuitka | 4.1.3 |
| Compiler | MinGW64 GCC 15.2.0 |
| NumPy | 2.4.6 |
| pco | 2.6.0 |
| pyserial | 3.5 |
| PyVISA | 1.16.2 |
| Pillow | 12.3.0 |
| OpenCV | 5.0.0.93 |
| pythonnet | 3.1.0 |
| zhinst | 26.4.1 (new LabOne; 18.12 still uses the separate Python 3.7 bridge) |

The V2 build package pins these Python dependencies in `requirements_build_v2.txt` and creates a separate
`.build_venv` inside the package, leaving the lab PC's existing Python environment untouched.

The first successful build produced a one-file GUI EXE of about 61.23 MiB. That original EXE was
64,207,360 bytes with SHA-256:

```text
B141A02D2EC6788592DD7DFE1C22319F4D9080E09DFA0F494CFD40926E05DA7E
```

This is tracking information for an old build product and must not be taken as the expected hash of the current `1.0.1 PCOFIX` EXE.

## 3. Standard build procedure on the lab PC

1. Install 64-bit Python 3.11 on Windows 11 x64.
2. Copy the whole extracted `MOKE_NH_V2_Windows_Build_Package` folder to a local disk.
   Building directly on a USB stick, network share or synced folder is not recommended.
3. Make sure the first build can reach the Python package index, GitHub and the Nuitka MinGW download,
   and reserve at least about 8 GB of disk space.
4. Double-click `BUILD_AND_INSTALL_V2.bat`; do not run Nuitka commands by hand.
5. Wait for all 10 stages of the script. A first build usually takes 30–90 minutes.
6. The build and installation count as passed only when `BUILD AND INSTALL COMPLETED SUCCESSFULLY` appears together with
   `PCO native DLL payload verification: PASS`.
7. Record the install path, file size and SHA-256 printed at the end of the script.
8. Start the program from the `MOKE NH Control V2` desktop shortcut, then do the hardware acceptance.

The batch script runs, in order:

1. locate 64-bit Python 3.11;
2. create or reuse `.build_venv`;
3. install the pinned dependencies and check the PCO native DLLs;
4. run the syntax, import, legacy LabOne bridge and 8 instrument-safety regression tests;
5. build the one-file GUI EXE with Nuitka, MinGW64, the Tk plugin and the V2 custom icon;
6. check the PCO DLLs in the Nuitka payload;
7. run the compiled EXE's PCO native-library self-test;
8. release and install the verified EXE and the MFLI 18.12 compatibility installer;
9. create the desktop shortcut;
10. print the installed file size and SHA-256.

## 4. Key Nuitka build settings

The key parameters in the current batch script must not be removed casually:

```text
--mode=onefile
--mingw64
--enable-plugin=tk-inter
--windows-console-mode=disable
--onefile-tempdir-spec={CACHE_DIR}/NBB/MOKE_NH/V2_200_INSTRUMENT_AUDIT_MFLI_PCOFIX
--user-package-configuration-file=pco.nuitka-package.config.yml
--include-package=pco
--include-package-data=pco
--include-package=serial
--include-package=pyvisa
--include-package=PIL
--include-package=numpy
--include-package=cv2
--include-package=pythonnet
--include-module=clr
--include-package=zhinst
--nofollow-import-to=*.tests
```

The V2 build package's file/product version is `2.0.0.0` and the compilation report goes to
`build/MOKE_NH_V2_compilation_report.xml`. V2 uses its own one-file cache folder so that
Windows does not reuse a stale cache with the V1 source or missing PCO DLLs.

## 5. A long pause at 94.6% is not necessarily a hang

The first build once stayed at `Backend C: 94.6%` for more than 30 minutes and then completed normally. This
stage is usually compiling about 488 C files or linking, not the Python main program hanging.

How to tell:

- no explicit `ERROR` has appeared;
- Python, SCons, GCC or linker processes still exist in Task Manager;
- CPU or disk still shows occasional activity.

If these hold, keep waiting. Do not force-close the window just because the percentage has not changed for a while. Treat it as an error only
when the processes have exited, the script prints a failure, or system resources show no change for a long time.

Nuitka WARNINGs about `*.tests` being excluded, undesirable imports or anti-bloat
are non-fatal; they also appeared in this successful build. The Nuitka return code, `completion="yes"` in the
report and the subsequent self-test results are what count.

## 6. PCO camera DLL failure: root cause and final fix

### 6.1 Old symptom

The old EXE opened the main GUI, but starting the camera failed with:

```text
Could not find module
'...\pco\win_x64\sc2_cam.dll'
(or one of its dependencies)
```

The error path is in the Nuitka one-file temporary extraction folder. Parts that do not depend on PCO, such as the LEDs, may still
work, so "the GUI opens" is no proof that PCO was packaged correctly.

### 6.2 Root cause

The old command relied only on `--include-package=pco` and `--include-package-data=pco`. Nuitka
does not collect DLLs completely as ordinary package data, while `pco.loader` loads these native libraries dynamically from
`pco/win_x64` via `ctypes.windll.LoadLibrary`, so the generated
EXE lacked `sc2_cam.dll` and its dependencies.

Inspection showed the official `pco 2.6.0` Windows wheel contains 27 DLLs:

- 21 under `pco/win_x64`;
- 6 under `pco/win_x64/genicam`.

These include at least `sc2_cam.dll`, `pco_recorder.dll`, `pco_conv.dll`,
`etc_xcite.dll`、`sc2_cl_me4.dll`、`sc2_clhs.dll`、`sc2_genicam.dll`、
`sc2_gige.dll` and the GenICam dependencies.

### 6.3 PCOFIX fix

`1.0.1 PCOFIX` adds `pco.nuitka-package.config.yml`, which explicitly collects the DLLs of the following two folders
as native payload:

```text
pco/win_x64/*.dll
pco/win_x64/genicam/*.dll
```

The build script also adds three release gates:

1. before compiling, confirm the source environment has at least 27 PCO DLLs and check the key files;
2. after compiling, confirm the DLL count in `build/MOKE_NH_V1.dist/pco/win_x64` is complete, the nested
   GenICam files exist, and the compilation report contains `sc2_cam.dll`;
3. run `--pco-dll-self-test` with the compiled EXE, actually loading SDK, Recorder,
   Convert and XCite; installation is allowed only when the log writes `RESULT: PASS` and the exit code is 0.

On success you should see something like:

```text
PCO source DLL count: 27
PCO payload DLL count: 27
RESULT: PASS
PCO native DLL payload verification: PASS.
```

If any check fails the batch script must stop; nothing is copied to the install folder and the desktop release shortcut is neither created nor
updated. Do not bypass these gates just to "get an EXE first".

## 7. Build products and desktop shortcut

After success there are normally three EXE copies:

```text
Build output:    <build package>\build\MOKE_NH_V2.exe
Release copy:    <build package>\dist\MOKE_NH_V2.exe
Stable install:  %LOCALAPPDATA%\NBB\MOKE_NH_V2\MOKE_NH_V2.exe
```

The desktop shortcut `MOKE NH Control V2.lnk` points at the stable install copy under `%LOCALAPPDATA%`
and uses the EXE's embedded icon, so the shortcut keeps working if the build folder is later moved or deleted.
If overwriting the install fails, exit every running `MOKE_NH_V1.exe` first and retry.

Ordinary users need only the final EXE and the installed vendor drivers, not the build package. The build package contains source
and should only be given to maintainers. One-file compilation raises the bar for casual source inspection but is not
absolute protection against reverse engineering.

## 8. External drivers still required after a successful build

The Nuitka EXE cannot replace these vendor runtimes:

- PCO Camera/SDK and the actual camera interface driver;
- NI-VISA or the VISA/GPIB backend used in the lab;
- USB serial device drivers;
- Thorlabs Kinesis；
- the Microsoft Visual C++ Redistributable x64 where needed.

Three conclusions must therefore be kept apart:

- `Nuitka build succeeded`: only means the EXE was generated;
- `PCO self-test PASS`: means the packaged PCO native DLLs load from the EXE;
- `real camera acquisition succeeded`: additionally means the lab PC's drivers, interface, cables, power and camera are all fine.

The user has confirmed that `1.0.1 PCOFIX` built successfully. If real PCO camera acquisition has not been done yet,
a hardware acceptance on the lab PC is still required; the self-test alone does not prove the whole hardware chain.

## 9. Release acceptance checklist

Every release must complete at least these checks:

- [ ] build package SHA-256 matches the handoff notes;
- [ ] Python is 64-bit 3.11;
- [ ] the script completes 9/9 stages with no `ERROR`;
- [ ] PCO source DLLs and payload DLLs both number at least 27;
- [ ] `MOKE_NH_V2_pco_self_test.txt` contains `RESULT: PASS`;
- [ ] final EXE size and SHA-256 recorded;
- [ ] desktop shortcut points at the `%LOCALAPPDATA%` stable install copy;
- [ ] double-click needs no password and the main GUI and every page open normally;
- [ ] the PCO camera actually opens and acquires continuously;
- [ ] NI-VISA, serial and Kinesis devices connect one by one with low-risk parameters;
- [ ] after closing the program every output module stops safely as expected;

## 10. Maintenance principles

- Day-to-day development edits only `MOKE_NH_dev.py`; never overwrite the frozen V1/V2 build packages directly.
- When forming the next stable version, first copy out the frozen release source, then update the file version, product version,
  one-file cache folder and build revision so different versions never share a stale cache.
- After updating `pco`, Nuitka, NumPy, OpenCV or Python, re-check the PCO
  wheel's DLL count and folder layout and pass the compiled-EXE self-test again.
- Do not delete `pco.nuitka-package.config.yml` and do not replace it with a plain
  `--include-package-data=pco`.
- After a successful build, record the Windows/Python/Nuitka/dependency versions, EXE size, SHA-256,
  PCO self-test result and hardware test conclusions, then add them back to this handoff document.

## 11. References

- Nuitka Package Configuration：<https://nuitka.net/user-documentation/nuitka-package-config.html>
- Nuitka User Manual：<https://nuitka.net/user-documentation/user-manual.html>
- PCO Python package：<https://pypi.org/project/pco/>
- PCO SDK manual：<https://www.excelitas.com/file-download/download/public/102647?filename=pco_sdk_Manual.pdf>
