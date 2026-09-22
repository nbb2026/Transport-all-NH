# Magneto-Electro-Optical Transport V3 revision 3.2.0 release notes

Build revision: `3.2.0 PY3119 NUITKA413 MINGW64 PCOFIX ROT90 SCANLOG`

## Scope

- Frozen from `3-Transport/Magneto_Electro_Optical_Transport_NH_dev.py` on
  2026-08-27.
- One-file Windows x64 EXE with no startup password.
- Includes the integrated illumination/camera, motor, MOKE, Keithley 2410,
  Keithley 6221, IV, Gate-VI and PPMS1 pages present in the frozen source.
- Bundles the Python clients for PCO, VISA, Zurich MFLI and optional MultiPyVu.
- Preserves external vendor-driver and server requirements.
- Replaces the earlier V3 build package while retaining the V3 application
  and shortcut name; internal file/product version is 3.2.0.0.

## Logic changes since the previous V3 package

- PCO frames are rotated 90 degrees counterclockwise before every background,
  difference, ROI, display, recording and save path. Physical corner labels
  follow the transformed quadrants as NW/SW/SE/NE = 4/1/2/3, and the 100x FOV
  marker uses the matching rotated calibration.
- MOKE parameter logs retain all round-trip scan settings and now include
  non-querying hardware snapshots at scan start and export: all four LED
  channels plus serial state, both BOP/Gauss paths, physical selector, safety,
  ports, targets, steps, cached verified currents, displayed fields,
  connection state and output-arm state.
- Scan-start snapshot timing now follows activation of scan ownership flags,
  so the recorded active channel and running state are correct. Export logs
  also summarize verified-current range, measured-field range, valid ROI
  points and total ROI samples.
- Logging and file export continue to use cached/UI state and the dedicated
  file worker; saving does not inject extra serial/VISA queries into a scan.

## Build and verification

- Required build interpreter: CPython 3.11.9 x64.
- Compiler flow: Nuitka 4.1.3 + MinGW64 + one-file.
- Build script runs syntax checking and 28 offline release tests.
- PCO native DLL presence is checked before and after Nuitka compilation.
- The compiled EXE must pass the PCO native-library load self-test before it is
  published.
- The EXE, SHA-256 sidecar, PCO self-test log and Nuitka compilation report are
  published together under `V3_Compiled_Output`; the Desktop shortcut targets
  the EXE in that folder.
- The application and Camera Live titles end with
  `-NH & Prof Chen's group` for laboratory ownership identification.
- The Windows executable uses a new Transport-wide magnet/optics/device icon
  with a transparent background and no embedded version badge.

## Acceptance status

The package can be prepared and statically/offline tested on macOS, but the
Windows EXE itself must be compiled on Windows x64. Compilation success does
not establish real-camera, real-VISA, MFLI, Kinesis, PPMS or output acceptance.
