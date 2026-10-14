# RT-E: room-temperature electrical transport

Entry point: `../Transport_all_NH_dev.py`, tab **RT-E**, class `RTEPanel`.

## Instruments and scans

- Reuses the PPMS measurement stack: 6221 AC excitation, three independently
  selectable SR830 slots, and Zurich MFLI (including simultaneous 1+2 / 1+2+3).
  The auxiliary 2400 connection is retained. Harmonic changes apply without a
  reconnect. SR830 line-notch and sync filters remain off.
- Uses the STFMR KEPCO 4886 and LS475 controllers. Initial addresses match STFMR:
  KEPCO `GPIB0::6::INSTR`, LS475 `GPIB0::15::INSTR`; both are editable.
- Start-up settings (2026-10-07): 6221 0.1 mA RMS at 17.3 Hz, compliance 50 V;
  the Zurich alone measures, reading harmonics 1+2+3 together; SR830-1/2/3
  are not ticked (their harmonics stay 1+2+3, 2 and 3); KEPCO 0 A, 5 V limit.
  The PPMS page keeps 317.3 Hz.
- RT-E Zurich defaults to `MF-DEV4414`, Data Server `192.168.68.250:8004`.
  This endpoint override belongs to RT-E alone; PPMS, STFMR and other modules
  retain their existing server selection. The measurement PC has confirmed TCP
  reachability; the LabOne API handshake still requires hardware verification.
- No PPMS connection, temperature, helium, persistent-mode, macro or motor UI.
  Common measurement-state names retain the `ppms_` prefix internally; RT-E
  does not connect to or issue commands to the PPMS environment controller.
- **Field**: scan KEPCO programmed current in A; measure and plot signed LS475
  field in G. There is no assumed G/A calibration or field-target feedback.
  Limits are +/-10 A and a positive voltage limit up to 20 V, inherited from
  STFMR. The voltage limit is not an independent coil-power safety check.
- **Current**: scan 6221 excitation in mA RMS; record LS475 field without
  issuing any KEPCO output/setpoint commands. KEPCO need not be connected.
- Supports forward/reverse ranges, an optional round trip, loop count, settle
  time and acquisition duration. Field scans use the existing STFMR field
  stability tolerance, consecutive-read count and timeout.
- Each point's reading waits max(Settle s, 1 x the slowest lock-in time
  constant measured) after its field, current, angle or harmonic change
  (2026-10-14): 1 s at the 1 s defaults. The user chose 1 x TC on every
  page ("change all to 1*TC please"; it was 7 x TC earlier that day): a
  sweep reading lags by only a fraction of a step, and the first point of
  each pass still takes agreeing windows. The Field/Current status line and
  the Angle Yes/No show the wait and the shortest duration; CSV rows end
  with `effective_settle_s` and `slowest_lockin_tc_s`. Each scan start (and
  Set I) reads every measured SR830's time constant with OFLT? (a query, no
  write), so one changed on the front panel since Connect is waited for,
  logged, recorded in `slowest_lockin_tc_s`, and shown on the status line
  during that wait ("Each reading now waits 10 s: 1 x 10 s TC of
  SR830-1"). See "Lock-in settle: at least 1 x TC on every page" and the
  superseded "RT-E and PPMS: lock-in readings settle at least 7 x TC" in
  `README.md`.

## Saving and lifecycle

- Select a folder and optional filename before hardware is configured. A blank
  name uses a timestamp; an existing name gets a numeric suffix.
- Each completed point is written and flushed/fsynced to UTF-8 CSV. Partial
  or cancelled points are not published. CSV preserves all Zurich demodulator
  columns and disabled-channel markers, as in PPMS. Plot rows follow the
  selected harmonic orders and contain X/Y/phase (R remains in CSV).
- A completed **Current** scan turns the 6221 off (0 mA), confirmed by
  `OUTP?` (2026-10-07). A completed Field scan and **Stop (hold outputs)**
  retain excitation and magnet output. Explicit OFF buttons and application
  shutdown turn owned outputs off. A measurement error stops 6221 excitation but leaves the magnet setting;
  KEPCO/field-settling failures also attempt KEPCO Zero/OFF.
- Rack commands use one worker so shutdown follows in-flight scan commands.
  Shared instrument leases prevent simultaneous ownership by RT-E and another
  tab; disconnect the instrument on its former tab before connecting here.
- No real hardware acceptance is implied by offline tests. Verify addresses,
  cooling, polarity, voltage/current limits and actual field behavior at low
  current before the first experiment. Windows GUI layout remains to be checked.

## Offline regression

From the `0-Transport-Code/Other-files` directory, with the environment set as in README.md:

```sh
PYTHONDONTWRITEBYTECODE=1 ../.pyenv/versions/3.11.9/bin/python3.11 -m unittest discover -s . -p 'test_rte_panel.py'
```

Tests use simulated controllers/widgets and temporary CSVs. They never open a
VISA/LabOne connection. Existing PPMS and STFMR regressions should also pass.
# Continuous current sweeps

## Sequential SR830 harmonics

Each RT-E SR830 selector supports 1, 2, 3, 1+2 and 1+2+3. Multi-order
selections run the complete scan plan (all loops and any return leg) once per
order, not multiple orders at each point. Mixed selections run in parallel
by pass index; a meter stops participating when its selected orders are done.
Zurich, if selected, continues recording its simultaneous demodulators in
every pass. CSV rows include `harmonic_pass` and each participating SR830's
actual configured harmonic; finished meters have empty cells in later passes.
Plots separate passes and harmonic orders. The 6221 is initialized only once;
subsequent passes change SR830 harmonics with readback verification and update
current amplitude without restarting the source. First-point settling repeats
for each pass. Hardware validation is still required.

RT-E Current scans initialize the 6221 once, reserving a fixed source range
for the largest planned peak current while the output is still off. Later
points update only `SOUR:WAVE:AMPL`, including return legs and additional loops;
they do not reset, abort, re-arm, or turn the output off. Amplitude readback and
existing acquisition/compliance checks remain active. A larger fixed range may
reduce resolution at the smallest currents compared with per-point ranging.
Initial scan setup and fault shutdown remain unchanged. Hardware continuity
must be verified on the measurement PC; offline tests do not establish it.
