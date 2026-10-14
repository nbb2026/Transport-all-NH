# Magneto-Electro-Optical Transport Experiment Control Directory

## STFMR: Settle s and Acquire s replace Wait/pt; 7 x TC settle; table in order (2026-10-14)

- **The user asked:** "add a acquire time in the ST-FMR scan mode, default
  is 2s. also add a settle time and default 1s. delete the delay time." and
  "also re-arrange the GUI buttons and windows in the ST-FMR sweep param
  module and make them in order." With the 1 s lock-in time constants of
  the same day the user chose one rule for every page: every lock-in
  reading waits at least 7 x TC (`LOCKIN_SETTLE_TAUS`; 99 % settled at
  12 dB/oct) after a change, or Settle s when that is longer. This section
  replaces the STFMR timing given in the 1 s section below (Wait/pt, 5 x TC,
  the MFLI alone at half a TC).
- **"Wait/pt s" is gone** (`stfmr_lockin_delay_var`,
  `STFMR_LOCKIN_DEFAULT_DELAY_S`). New boxes: **"Settle s"** (default 1,
  0 to 3600 s) and **"Acquire s"** (default 2, above 0 and up to 3600 s;
  the 6221-IV page's bounds). Both are read at Start and are locked while a
  sweep runs, as the SR830 TC is. A word, an empty box, nan/inf or a value
  out of range is refused before any file or output.
- **Each point:** after its current step it waits the effective settle =
  max(Settle s, 7 x the TC of every lock-in it reads: the SR830 TC when the
  SR830 is read, "MFLI TC s" when the MFLI is). The STFMR-only
  `STFMR_LOCKIN_SR830_SETTLE_TAUS` (5) is gone; the MFLI now follows the
  same rule (it waited Wait/pt alone). Then it reads the field once and the
  lock-in(s) every 0.1 s (`STFMR_LOCKIN_SAMPLE_INTERVAL_S`; a reading that
  takes longer is followed at once) until Acquire s has passed since its
  first lock-in reading started, and saves the mean X and Y of each meter;
  R and theta come from the mean X and Y (as RT-E's
  `_average_ppms_transport_sample`). In MFLI + SR830 mode each reading is
  the MFLI then the SR830, so both are averaged over the same window. The
  waits are `root.after` calls as before; the window never blocks.
- **Every reading of the window passes the old checks.** SR830 or MFLI +
  SR830: an unlocked, overloaded or invalid reading ends the sweep through
  the usual output-off sequence, as a failed read did, and the point is not
  saved. MFLI alone, as before: no stop; the row's `reference_valid` is
  True only if every reading was locked, `mfli_input_overload` is True if
  any reading was overloaded (None if any was unknown), and
  `mfli_input_utilization` is the highest. A range change in the window
  drops the point's readings and starts the point again, field included,
  after the range re-read wait: max(effective settle, 10 x MFLI TC, 0.2 s)
  after an MFLI change, max(effective settle, 0.1 s) (so at least 7 x TC)
  after an SR830 change, and max(0.1 s, effective settle, 10 x MFLI TC)
  after an SR830 change in MFLI + SR830 mode.
- **Ranging pace:** only a point's first reading may step a range down;
  the later readings of its window step a range only up (overload or near
  full scale), still at most three MFLI range changes per point. Counted on
  every reading, a weak MFLI signal (always under 15 % of the lowest range)
  would run the firmware autorange, which counts as a change even when it
  keeps the range, after every three readings and restart each point up to
  three times, 10 s each. `STFMRSR830Controller.read_sample` takes
  `allow_down=False` for these readings; the MFLI's are handled in
  `_stfmr_lockin_read_mfli` (the shared `read_stfmr_mfli` is unchanged).
- **Discarded readings** (the start pre-read and the first two readings of
  each loop, carrier and modulation frequency) stay one reading each: no
  window, as their value is never used. The first reading of each pass
  still follows the pre-read at once; every later one follows a step and
  its settle, as with Wait/pt.
- **The MFLI's demodulator rate:** `configure_mfli` sets it to 5 x the read
  rate (at least 10 Hz). The read rate was 1 / Wait/pt (2 Hz at 0.5 s, so
  10 Hz); it is now 1 / 0.1 s, so 50 Hz, recorded as `mfli_sample_rate_hz`
  = 10 in the run log.
- **Start confirmation:** a line such as "MFLI TC: 1 s; each point settles
  7 s (7 × TC 1 s; Settle s 1 s) then averages 2 s" for every meter (in
  dual mode both TCs are named), and an estimate that counts a settle per
  step and Acquire s per saved point, e.g. "Estimated at least 5398.0 s
  (600 settles of 7 s + 599 averages of 2 s; about 1.5 h)."
- **Timing at the page's defaults** (MFLI, 4 GHz, -3 to 3 A in 0.01 A =
  601 currents, Loop 1, Settle 1 s, Acquire 2 s, both TCs 1 s): each point
  settles 7 s and averages 2 s (21 readings), about 9 s plus the reads,
  so the Start estimate is 600 x 7 s + 599 x 2 s = 5398 s, about 1.5 h per
  frequency and modulation frequency (it was 600 x 0.5 s = 5 min for the
  MFLI alone, 50 min with the SR830). The MFLI's first-point range check at
  each pass start re-reads after 10 s. SR830 and MFLI + SR830 settle the
  same 7 s at 1 s TCs.
- **Records:** the live CSV gains four columns at the end, after
  `sr830_time_constant_s`: `settle_s`, `effective_settle_s`, `acquire_s` and
  `sample_count` (readings in that row's mean, per meter); every older
  column keeps its place (there was no Wait/pt column). `timestamp_iso` is
  the time of that meter's last reading of the window. The run log's
  settings drop `point_delay_s` and `effective_point_wait_s` and gain
  `settle_s`, `acquire_s`, `settle_time_constants` (7), `effective_settle_s`
  and `acquisition_sample_interval_s` (0.1).
- **The parameter table, in measurement order** (at most five pairs a row;
  a sixth made the table too wide for the 1101 px window):
  1. Microwave: f start GHz, f stop GHz, f step GHz, Power dBm.
  2. Magnet sweep: Start A, Stop A, Step A, Loop, Comp. V.
  3. Modulation and timing: Mod. start Hz, Mod. end Hz, Settle s,
     Acquire s.
  4. SR830: Meter, SR830 REF IN, SR830 TC, SR830 autorange (Meter leads
     this row; it says which of rows 4 and 5 are used).
  5. MFLI: MFLI reference, MFLI TC s, MFLI autorange.

  Then the actions in order: Start STFMR multi-frequency sweep, Stop / zero,
  Save CSV; Read LS475 with Continuous field read; the readouts and lamps.
  They sit below the table in the 1101 px window and to its right when the
  window is wide enough (2000 px), as before. The `parameter()` helper and
  the column minimums are kept: the SR830 TC dropdown's minimum moved with
  it to column 5, and column 7 keeps the Loop spinbox whole (the dropdown,
  as wide, guarded it there before). The table is about 40 px narrower
  (Comp. V ends the widest row instead of Mod. start Hz) and one row
  (about 38 px) taller, which the plots give up: in the last Windows CI
  shot (1101 x 1300 px window) each plot canvas was 143 px tall, so about
  124 px now, and about 164 px in the lab's 1380 px tall window.
- **Tests:** `test_stfmr_settle_acquire.py` (new): the settle rule for
  SR830, MFLI and both, through the shared constant; the confirmation line
  and estimate (the page's default sweep included); the default sweep
  (7 s settle, 21 readings 0.1 s apart); means of X and Y with R and theta
  from the mean, for each meter's spelling and both together; a range change
  in the window restarting the point (SR830 and MFLI); the ranging pace; a
  failed or unlocked reading stopping with the outputs off; stop between
  readings; the bounds of both boxes; the averaging helper; the SR830
  controller's `allow_down`. Updated: `test_stfmr_sr830_tc_fixed_current`,
  `test_stfmr_mod_prime_range` (the table rows, the CSV header, the
  estimate), `test_lockin_time_constant_defaults`, `test_fmr_stfmr_minors`,
  `test/test_instrument_safety_logic` and the shared STFMR harness in
  `test/test_stfmr_realtime_recording` (its Root now keeps time, so the
  window runs on the test's clock; Settle 0 and Acquire 0.2 s there). The
  CI GUI test `test_stfmr_field_output` checks the drawn table rows, the
  SR830 TC beside its REF IN, the actions below the table at 1101 px and
  plots of at least 55 px (not run here: it needs a display).
- **Review fixes (2026-10-14):**
  - A reading whose checks raise (in MFLI + SR830 mode a missing or
    non-finite X or Y did: `_stfmr_lockin_sample_component` raises rather
    than returning nan) no longer escapes the Tk callback. It left the
    sweep running with nothing scheduled, and Stop then neither zeroed the
    4886 nor switched the RF off. The dual X/Y/Theta check now counts such
    a value as invalid and ends the sweep through the output-off sequence
    ("Dual-meter reading contains invalid X/Y/Theta; current point not
    saved"), and `_check_stfmr_lockin_sample` wraps every reading's checks
    so any other failure there ends it the same way. The real read paths
    already reject non-finite values, so this was latent, but the check now
    runs on every reading (about 21 a point).
  - **"MFLI TC s" is locked during a sweep**, as Settle s, Acquire s and
    the SR830 TC are: 7 x it is the settle of every point that reads the
    MFLI, and a value typed mid-sweep showed a TC the sweep was not using.
  - **Time left in s, min or h:** the status line read "remaining about
    33000.0 s" for a 7-carrier sweep at about 9 s a point, about as wide as
    the 52-character label. It now reads "remaining about 9.2 h" (one
    modulation frequency) or "~9.2 h left" (several), from the same helper
    (`_stfmr_lockin_duration_text`: seconds under 2 min, minutes under 1 h)
    as the Start estimate's "about 1.5 h".
  - New tests in `test_stfmr_settle_acquire.py`: a near-full-scale MFLI
    reading later in the window runs the firmware autorange as
    "overload/high", re-reads after 10 s and saves an uncontaminated mean;
    near full scale on every attempt, the fourth range change of one point
    ends the sweep with the outputs off ("after 3 retries"); an SR830 range
    change later in a dual window restarts both meters after 10 s with
    paired, uncontaminated rows; a non-finite X (MFLI or SR830) in a dual
    window and any raising check end the sweep with the outputs off and
    nothing scheduled; the duration text. Updated: the lock test
    (`test_stfmr_sr830_tc_fixed_current`), the status-line patterns and a
    width check of hours left (`test_stfmr_mod_prime_range`), and the CI GUI
    test `test_stfmr_field_output` (the MFLI TC entry is the kept one).
- **Not yet verified:** on the lab PC with the instruments (the read pace a
  real GPIB bus allows, the MFLI at 50 Hz), and the CI screenshots.

## Non-local: lock-in readings settle at least 7 x TC (2026-10-14)

- **The user's choice:** "every lock-in reading waits at least 7 x TC (99 %
  settled at 12 dB/oct) after a change, on every page; Wait/pt or Settle s
  still apply when longer; ... the Start confirmation shows the wait". This
  section covers the Non-local page: 6221-IV and Gate-VI. 2400-IV and
  |R| vs time read the Keithley (DC, no lock-in) and are unchanged. The
  factor is the shared `LOCKIN_SETTLE_TAUS = 7.0`; the wait is
  `CombinedControlApp._nonlocal_lockin_settle_s`. A change is a step of the
  swept quantity, the 6221 switching on, or a lock-in range step.
- **Which time constant:** the slowest among the lock-ins the scan
  measures. The Zurich: the rack's "TC (s)" box. A Gate-VI SR830: the 1 s
  Gate-VI writes (OFLT 10, now `NONLOCAL_VI_SR830_TIME_CONSTANT_CODE`). A
  6221-IV SR830: its front-panel value, read with OFLT?
  (`NonLocalACController.read_sr830_time_constants`, a query only). The
  2182A is not a lock-in.
- **Range steps (both scans):** the reading after an autorange step waits
  `_nonlocal_range_settle_s`: 7 x TC of that SR830 (RT-E's 5 x TC, still
  `NONLOCAL_IV6221_RANGE_SETTLE_TAUS["sr830"]`, raised to
  `LOCKIN_SETTLE_TAUS`) or 10 x TC of the Zurich (kept). On 6221-IV that
  was 5 x TC for an SR830. On Gate-VI there was no wait at all: only the
  reading that ranged was left out and the average went straight on. Now
  a range step during Measure s drops the pass's readings so far from the
  means (the raw CSV keeps every reading, with `auto_range_action`), waits,
  and starts Measure s again, as 6221-IV restarts its window. After 30
  restarts in one pass (`NONLOCAL_IV6221_MAX_RANGE_STEPS`; a meter that
  ranges on every reading) the pass reads on as before, so a long Gate-VI
  run does not stop for it.
- **6221-IV:**
  - **Every point** now waits max(Settle s, 7 x TC) after its current step,
    then averages Acquire s. At the defaults (the Zurich alone, TC 1 s,
    Settle 0.5 s, Acquire 1 s) that is 7 s; it was 0.5 s. At 12 dB/oct the
    first reading then shows 99 % of the step (it was about 26 % on
    average). The Zurich runs at 12 dB/oct (filter order 2). An SR830 keeps
    its front-panel slope: 7 x TC is 99.9 % settled at 6 dB/oct but only
    about 97 % at 18 and 92 % at 24, so Start reads the slope too (below).
  - **Far points:** the first point of each pass and each loop's jump back
    to Start still wait max(Settle s, 10 x TC) = 10 s
    (`NONLOCAL_IV6221_FIRST_SETTLE_TAUS`), and in a frequency scan at least
    5 periods.
  - **Start:** with an SR830 ticked Measure, Start first reads its
    front-panel time constant and slope (OFLT?, OFSL?; queries only:
    `read_sr830_time_constants`, `read_sr830_filter_slopes`) on the page's
    worker thread, as the rack's Zurich poll reads. Meanwhile the page is
    busy, as during a connection: both Starts, Set 6221, the rack's
    Connect/Disconnect, the 6221-IV Measure ticks and harmonics and the
    Zurich Ref./TC are held, and a Gate-VI Start that gets through is
    refused ("The page is busy"). Stop or OFF during the read switches the
    outputs off and cancels this Start ("6221-IV not started: the outputs
    are being switched off"). A failed read shows a warning and starts
    nothing. The confirmation and the folder dialog then open from the Tk
    event loop (`root.after`), not inside `drain_ui_queue`, so live status,
    plots and logs on every page keep updating while they are open. The
    scan reads the TC again at its set-up and waits the longer of the two,
    so it never waits less than the confirmation said.
  - **Confirmation:** "Each point waits 7 s after its current step (7 × the
    slowest lock-in TC, Zurich TC 1 s, or Settle 0.5 s if longer), then
    averages Acquire 1 s" and "The first point of each pass waits 10 s
    (10 × the slowest lock-in TC, or Settle 0.5 s if longer)" (with "and
    each loop's jump back to Start" for one-way loops; it said "(10 × TC)"
    even when Settle s set the wait). With an SR830 measured, a line such
    as "SR830 front panel (read back): SR830 #8 TC 3 s, 12 dB/oct", and
    when its slope leaves a point under 99 % settled, a note such as "Note:
    at 24 dB/oct SR830 #8 shows only about 91 % of each step when the point
    is read (7 × TC gives 99 % at 12 dB/oct); set 12 dB/oct on its front
    panel, or raise Settle s". The estimate counts each point's wait, the
    far points and the first point's second window. The default 100-point
    scan is about 13 min 54 s (99 x 8.3 s + 11.3 s + 1 s); it showed
    3 min 11 s. With an SR830 front panel at 3 s each point waits 21 s and
    the far points 30 s.
  - **Records:** the CSV has a new last column, `settle_waited_s` (the wait
    after each point's current step). The parameter log adds
    `point_settle_s`, `first_settle_s`, `lockin_time_constant_s`,
    `slowest_lockin`, `sr830_time_constants_s` and
    `sr830_filter_slopes_db_per_oct`.
  - **Unchanged:** the Settle s and Acquire s defaults.
- **Gate-VI:**
  - **Each point:** the lock-ins start from zero when the 6221 read current
    switches on after the pulse (its output_off before the pulse also
    stopped the Trigger Link reference). The lock check now comes right
    after the switch-on, and the reading starts once the 6221 has run the
    6221 pre-run s since the switch-on and the lock-ins have had 7 x TC
    since the lock was confirmed (in a frequency scan, also at least
    5 periods). It used to wait the pre-run, check the lock and read at
    once, so a Zurich that took 3 s to lock had about 4 TC of valid
    reference. At the defaults (pre-run 5 s, SR830 1 s, Zurich TC 1 s) that
    is 7 s plus the lock check (an SR830 locks within a few cycles); it was
    5 s. The scan uses the time constants read back after its set-up when
    they are longer.
  - **Later SR830 orders** still wait max(pre-run, 10 x TC) = 10 s
    (`NONLOCAL_VI_HARMONIC_SETTLE_TAUS`), already more than 7 x TC.
  - **Confirmation:** a new line, "Each point: the 6221 runs at least 7 s
    before the reading (7 × the slowest lock-in TC 1 s = 7 s from the
    reference lock, or 6221 pre-run 5 s from the switch-on if longer), then
    reads Measure 30 s". The estimate counts it (not the lock check). The
    default scan (±100 V in 10 V steps, 41 points x 2 loops) takes 54.4 s
    per point, about 1 h 14 min 37 s in all; it was 1 h 11 min 53 s.
  - **Records:** `VI_raw.csv` and `VI_summary.csv` have a new last column,
    `settle_waited_s`: the time from the 6221 switch-on to the reading (the
    lock check and the wait after it, to 1 ms), or the order's settle for
    an SR830's later orders. `VI_parameters.log` adds
    `lockin_time_constant_s`, `lockin_settle_s` and `effective_settle_s`.
  - **The 2182A alone** (a DC run) keeps the 6221 pre-run.
- **Tests:** `test_nonlocal_lockin_settle.py` (new, Tk-free) checks the
  rule, the 6221-IV waits, estimate, confirmation (the far-point line with
  a longer Settle s, the SR830 slope line and note), SR830 OFLT?/OFSL?
  reads and records, the page held busy during Start's read (Gate-VI Start
  and Set 6221 refused, an OFF cancelling the Start, a failed read freeing
  the page), the dialogs opened from the event loop, the range-step waits
  of both scans (a Gate-VI range step dropping the pass, a meter ranging
  on every reading not stopping the run), the Gate-VI lock check before
  the settle, and the Gate-VI pre-run, estimate, confirmation, worker
  waits and CSV columns. Tests of other timing turn the 7 x TC wait off
  (`LOCKIN_SETTLE_TAUS` patched to 0): `test_nonlocal_6221_iv`,
  `test_nonlocal_frequency_scan`, `test_nonlocal_gate_vi_harmonics`,
  `test_nonlocal_pulse_gate_6221` and `test_nonlocal_minor_review`. The
  6221-IV far-jump test now expects 21 s ordinary points at a 3 s TC,
  `test_nonlocal_frequency_scan` counts the Gate-VI 5-period wait less the
  lock check, `test_nonlocal_gate_vi_loops` expects the new last column,
  and `test_lockin_time_constant_defaults` checks the 7 s default wait. No
  CI GUI expectation names these texts.
- **Supersedes:** the 6221-IV "Other points wait Settle s" and Gate-VI "No
  Gate-VI wait comes from the Zurich TC" notes in the next section, and
  "5 x TC of that SR830" after a 6221-IV range step in older sections.

## RT-E and PPMS: lock-in readings settle at least 7 x TC (2026-10-14)

- **The user's choice:** "every lock-in reading waits at least 7 x TC (99 %
  settled at 12 dB/oct) after a change, on every page; Settle s still
  applies when longer; the Start confirmation shows the wait". This section
  covers RT-E and PPMS. STFMR and Non-local are changed separately.
- **The rule:** after every change of the swept quantity (each new field,
  temperature, angle or current setpoint, the first point included, and the
  first point of every harmonic pass), the wait before the reading is
  max(Settle s, 7 x the slowest time constant among the lock-ins that scan
  measures) (`ppms_lockin_settle_s`, with the shared `LOCKIN_SETTLE_TAUS =
  7.0`). At 12 dB/oct a step has then settled to 99 % (1 - 8 e^-7).
- **Which time constants:**
  - **PPMS SR830s:** the value each one last read back with OFLT?: at
    Connect, at an 'SR830 TC' change and at every scan start. The scan
    start read also sees a change made on the front panel. An SR830 that
    has not read back yet counts with the 'SR830 TC' choice.
  - **RT-E SR830s:** the same: Connect sets OFLT 10 = 1 s and reads it
    back, and every scan start (and Set I) reads OFLT? again, one query and
    no write, as PPMS does. Before the review of this change RT-E read no
    OFLT? at the scan start, so an SR830 changed to 10 s on its front panel
    or by another program after Connect still got 7 s instead of 70 s, and
    its rows recorded `slowest_lockin_tc_s` = 1 as if read back. A changed
    value is logged ("RT-E SR830-1: the time constant reads 10 s (OFLT
    12), not the 1 s last set ..."); an invalid reply stops the scan start
    before the 6221 turns on, as on PPMS.
  - **The Zurich (both pages):** its TC box (`zurich_scan_tau`), when the
    Zurich is measured.
  - A harmonic pass counts only the lock-ins it measures. A later pass
    whose slower SR830 has finished its orders waits less.
- **Where it applies:** RT-E: the wait before each point's reading, which
  was Settle s. It comes after the KEPCO step and the LS475 field settling
  (Field), the 6221 amplitude step (Current) or the stage move (Angle).
  PPMS: the wait after the field, temperature, angle or current is reached
  and before the reading, in every mode (Temperature, Field, Angle,
  Current, the combined scans and Macro SCAN lines, which run the same
  Start).
- **Unchanged:** the PPMS temperature and field stability criteria (stable
  for max(1 s, Settle s)), the magnet's own settling, the LS475 field
  settling, the stage moves and the autorange waits. The first point's
  agreeing windows still follow the wait, so no existing wait is shortened.
  No instrument write is added, removed or reordered; the only new command
  is RT-E's OFLT? read at the scan start (a query beside HARM?/FMOD?/
  RSLP?). Outputs are as before on every path. A Stop during the wait ends
  the scan as before.
- **Start:**
  - **PPMS:** the confirmation has a new line, e.g. "Wait before each
    reading: 7 s (7 x 1 s, the time constant of SR830-1, the slowest
    lock-in measured; Settle s is 0.5 s)", or "(Settle s; 7 x the slowest
    lock-in time constant, … is 7 s)" when Settle s is longer. Passes with
    fewer lock-ins are named when they wait less. The duration estimate
    counts the wait at every point of every pass. The status line reads
    e.g. "Field scan: 41 points, settle 7 s; estimated about 22 min 16 s".
    The confirmation's old note on a Settle s shorter than 5 time constants
    of a 3 s or 10 s choice is gone: the scan now waits instead. The
    confirmation and estimate use the time constants known before the scan;
    the scan start reads them again (see below).
  - **RT-E Field and Current** still have no Yes/No. The status line shows
    the wait and the shortest the scan can take, e.g. "RT-E Current: 100
    points, settle 7 s; at least 13 min 21 s". "At least" counts every
    point's wait and Acquire s, and the second window the first point of
    each pass needs; the LS475 settling, the stage moves and the instrument
    reads come on top. The harmonic-pass count moved from this line to the
    log.
  - **Status lines round the wait** (`ppms_settle_status_text`, both
    pages): whole seconds from 100 s, 3 significant digits below (12345.7
    s shows as 12346, 0.0864199 s as 0.0864). With `{:g}` such values gave
    six digits, and the RT-E line became 505 to 521 px wide in Arial 15 px
    and was cut with an ellipsis. The confirmation, the log and the CSV
    keep the exact value. At 100000 points and Acquire s 86400 s the
    longest lines are "settle 86400 s; at least 4800024 h 00 min 00 s"
    (493 px in Arial, 590 px in DejaVu Sans) and "settle 0.0864 s; at
    least 2400026 h 24 min 02 s" (497 px, 595 px), under the 505 px /
    598 px of the longest status line already in the label. Only a wait of
    100000 s or more (7 x a 30 ks SR830 time constant), or one under 10 ms
    with Acquire s of 10 h or more, could be wider. The CI GUI test
    `test_panel_defaults.RTEStatusRowTests` checks these lines on the Linux
    (xvfb, DejaVu Sans) and Windows CI runs; it is not run here.
  - **RT-E Angle:** the Yes/No gains "Each angle waits … before it is read;
    the scan takes at least …". Its status line is unchanged, because the
    wait would not fit.
  - **Log:** the Start line gives the wait and how it was chosen. The scan
    logs a different wait only when one occurs: a front-panel time constant
    read at the scan start (either page), or a pass with fewer lock-ins.
  - **A different wait on the status line:** the same change also sets the
    status line for as long as that wait lasts, e.g. "Each reading now
    waits 70 s: 7 x 10 s TC of SR830-1" (or "... (Settle s)"). Before, an
    SR830 set to 10 s on its front panel since Connect left the
    confirmation's "7 s" and its estimate standing while each point waited
    70 s, with only a log line to show it. After the wait the first
    point's window line and then each saved point's line replace it, and
    their remaining time counts the longer wait.
- **CSV:** two new last columns on both pages, after every older column:
  `effective_settle_s` (the wait that point had) and `slowest_lockin_tc_s`
  (the time constant it came from, read back at the scan start on both
  pages). `stable_s` still records Settle s. The wait is rounded to
  1e-12 s, so a 100 ms time constant writes 0.7, not 0.7000000000000001,
  as the confirmation shows it.
- **Timing at the defaults** (Settle s 0.5 s, Acquire s 1 s, every time
  constant 1 s): each point waits 7 s instead of 0.5 s, 6.5 s more per
  point.
  - **RT-E** (the Zurich alone measures by default): Current 0.01 to 1 mA
    in 0.01 mA steps, 100 points: at least 13 min 21 s (was 2 min 31 s).
    Field -3 to 3 A in 0.05 A steps with the round trip, 241 points: at
    least 32 min 09 s (was 6 min 03 s), plus the field settling. Angle 0 to
    360° in 5° steps with the round trip, 145 points: at least 19 min 21 s
    (was 3 min 39 s), plus about 144 s of moves at 5°/s.
  - **PPMS** Field -15000 to 15000 Oe in 1500 Oe steps, 41 points from
    0 Oe at 100 Oe/s: about 22 min 16 s (was 17 min 50 s).
  - With a 3 s or 10 s 'SR830 TC' each point waits 21 s or 70 s.
- **Tests (Tk-free):** `test_lockin_settle_rte_ppms.py` (new): the rule
  (SR830 only, Zurich only, both, Settle s longer, an SR830 not read back
  yet, the words); each RT-E mode (Field, Current, Angle) through the real
  scan worker, the Zurich alone and with an SR830, each harmonic pass
  settling again with its own lock-ins, and the RT-E columns; each PPMS
  mode (Temperature, Field, Angle, Current, Field+Temperature) through the
  real nested and point workers on a clock that moves only when the scan
  waits, with the temperature and field stability waits unchanged, a
  longer Settle s, the Zurich, harmonic passes and a front-panel time
  constant read at the scan start; the PPMS confirmation, estimate, status
  line and log, and a Macro SCAN line; the RT-E status lines and the Angle
  Yes/No. The review round adds: the rounding to 1e-12 s (also through the
  PPMS worker at 100 ms) and the five-digit status format; RT-E waiting
  for a front-panel time constant read at the scan start, with the status
  line shown during the first wait; the status line set once per changed
  wait, and not after the window closed; the Angle+Temperature and
  Current+Temperature modes and PPMS Angle harmonic passes through the real
  workers; the Zurich "TC (s)" box read at Start into the wait, through
  RT-E's real `_rte_capture_zurich` and through `PPMSPanel`'s Start on the
  real page built on stand-ins; the rounded wait on both Start status
  lines. `test_ppms_sr830_tc.py`: the CSV columns (the point worker no
  longer waits in real time there) and the confirmation's wait instead of
  the old note; RT-E's scan start now reads OFLT? once with the same
  writes, and a front-panel change there is logged and waited for.
  `test_ppms_current_callbacks.py` and `test_ppms_rte_review.py`: their
  fake SR830s answer that OFLT? (1 s).
  `test_rte_angle_scan.py`: the new Field/Current status line and the
  Angle Yes/No; its stage-line test uses an 8 s Settle s, longer than
  7 x 1 s, announced as at Start. `test_panel_defaults.RTEStatusRowTests` (Linux and
  Windows CI only) checks the new longest lines, the sub-second one and
  the "Each reading now waits" line.

## Lock-in time constants: every default is 1 s (2026-10-14)

- **The user's choice:** "all default lock-in time constant change to 1s".
  Every lock-in time-constant box now starts at 1 s. A value typed or
  chosen by hand is used as before. Only the starting values changed:
  every wait computed from a time constant keeps its rule, so the waits
  below follow from the new values.
- **What changed (page, box: old -> new):**
  - **STFMR, "MFLI TC s":** 0.1 -> 1 s
    (`STFMR_LOCKIN_MFLI_TIME_CONSTANT_S`; also the value recorded when the
    MFLI is not used).
  - **STFMR, "SR830 TC":** 100 ms (OFLT 8) -> 1 s (OFLT 10)
    (`STFMR_LOCKIN_SR830_DEFAULT_TIME_CONSTANT`,
    `STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE`, which is also the default of
    `STFMRSR830Controller.configure`). It was a fixed 1 s until 2026-10-13,
    then a choice with 100 ms as the default; now 1 s again.
  - **Non-local, the rack's Zurich "TC (s)"** (used by 6221-IV and
    Gate-VI): 0.1 -> 1 s (`NONLOCAL_MFLI_TIME_CONSTANT_S`).
  - **PPMS "Time constant s" and RT-E "TC (s)"** (the Zurich row): 0.1 ->
    1 s (new `PPMS_ZURICH_TIME_CONSTANT_S`). The Zurich range-change wait
    also falls back to it when no TC is configured (it fell back to 0.1 s).
- **Already 1 s, left alone:** `NONLOCAL_LOCKIN_TIME_CONSTANT_S` (the
  default of `configure_mfli`). The PPMS and RT-E SR830s are set to
  OFLT 10 (1 s) on connect, then their front-panel value is used. Gate-VI
  writes 1 s to its SR830s. 6221-IV keeps each SR830's front-panel time
  constant; the program sets no value there. `configure_second_harmonic` and
  `configure_third_harmonic` take the TC of their page's box; they have no
  default of their own. The MOKE light, motor and magnet pages, CIDWM,
  MOKE-24 and FMR have no lock-in. `Test_Microwave_NH_dev.py` (a separate
  program, not changed here) still starts its "MFLI time constant s" at
  0.1 s.
- **Timing at the new defaults** (Wait/pt 0.5 s, Settle and Acquire as
  shipped):
  - **STFMR, SR830 or MFLI + SR830:** each point (all but the discarded
    first of each frequency) waits max(Wait/pt, 5 x TC) = 5 s (it was 0.5 s
    at 100 ms). An SR830 range change re-reads after the same 5 s. The
    Start estimate counts 5 s per point, so a default 601-current sweep
    takes about 50 min per frequency instead of 5 min.
  - **STFMR, MFLI range change:** the re-read waits
    max(Wait/pt, 10 x MFLI TC, 0.2 s) = 10 s (it was 1 s). In MFLI + SR830
    mode both re-reads (after an SR830 or an MFLI range change) wait 10 s
    (1 s at the old defaults).
  - **STFMR, MFLI alone:** MFLI is the page's default meter ("Meter"
    starts at MFLI), so a default STFMR sweep runs this way. Each point
    still waits Wait/pt alone (0.5 s). No STFMR rule ties it to the MFLI
    TC, so a point is now read half a time constant after the current step.
    A 12 dB/oct filter has then reached about 9 % of the step (about 96 %
    with the old 0.1 s). Raise Wait/pt to about 5 s for settled MFLI points.
  - **Non-local 6221-IV, with the Zurich measured** (by default the Zurich
    is the only meter measured, `NONLOCAL_IV6221_DEFAULT_MEASURE`): the
    first point of each pass and the jump back to Start at a new loop wait
    max(Settle s, 10 x the slowest TC) = 10 s (it was 1 s, unless an SR830's
    front-panel TC was longer). After a Zurich range step the reading waits
    10 x TC = 10 s (it was 1 s). A point still fails when its ranging would
    run past Acquire s + 30 s + 180 s, or after 30 range steps in a row. The
    Start estimate counts 10 s for the first point per pass and frequency
    (it counted 1 s). Other points wait Settle s (0.5 s) and average
    Acquire s (1 s), as before; no rule ties them to the TC. At the 1 s TC
    each of these points is therefore averaged 0.5 to 1.5 TC after its
    current step. Over that window a 12 dB/oct filter shows only about
    26 % of the step on average (about 100 % with the old 0.1 s). On a
    sweep each reading lags the current by about one point, and a round
    trip can show a hysteresis that is not in the sample. Raise Settle s
    to about 5 s for settled points (about 97 % of each step).
    (Superseded the same day: every point now waits at least 7 x TC; see
    "Non-local: lock-in readings settle at least 7 x TC" above.)
  - **Non-local Gate-VI:** the Zurich runs at 1 s (the log shows
    τ = 1 s). No Gate-VI wait comes from the Zurich TC. The 10 τ settle
    between SR830 orders uses the SR830s' 1 s, as before (10 s).
    (Superseded the same day: each reading now waits at least 7 x TC after
    the 6221 switches on; see the section above.)
  - **PPMS and RT-E, Zurich:** after a range change the reading waits
    max(0.2 s, 10 x TC) = 10 s (it was 1 s), at most 4 times per point, so
    up to about 40 s before "Zurich autorange did not settle". With idle
    autorange on (after "Set 6221" with a non-zero current), the idle
    readout also waits 10 s after each range change, up to 4 times. It
    holds the Zurich all that time, so a Zurich harmonic change or
    Disconnect clicked meanwhile (they queue on the same Zurich worker),
    and the Zurich set-up of a scan Start (it takes the same lock), can
    wait up to about 40 s (about 4 s at the old 0.1 s).
    The Zurich is read once per point, after Settle s and the acquisition
    time (0.5 s + 1 s by default). Those do not follow the TC, so at 1 s
    each Zurich reading comes about 1.5 TC after the setpoint is reached
    (15 TC with the old 0.1 s). On RT-E the Zurich is the only meter
    measured by default, so a default RT-E scan reads this way. PPMS
    measures its three SR830s too; they are set to 1 s on connect, as
    before. (Superseded the same day for RT-E and PPMS: each reading now
    waits at least 7 TC, see "RT-E and PPMS: lock-in readings settle at
    least 7 x TC" above.)
- **Tests:** `test_lockin_time_constant_defaults.py` (new) checks every
  changed default is 1 s: the constants, the method defaults, the STFMR and
  Non-local boxes in the source, the PPMS / RT-E box in the real panel
  constructor, and the waits above. `test_stfmr_sr830_tc_fixed_current`,
  `test_ppms_two_panels`, `test/test_instrument_safety_logic` and the STFMR
  harness in `test/test_stfmr_realtime_recording` (now at the 1 s defaults)
  expect 1 s. The 6221-IV page stub in `test_nonlocal_6221_iv` starts at
  1 s too. The CI GUI test `test_panel_defaults` checks that every box shows
  1 s (not run here: it needs a display). The same test file also checks
  the default meters above (STFMR MFLI, 6221-IV and RT-E the Zurich alone),
  how much of a step the default 6221-IV window sees, and that the idle
  PPMS / RT-E readout holds the Zurich for 10 s per range step.
- **Not yet updated:** root `README.md` still says the Non-local TC
  defaults to 0.1 s.

## PPMS: TC label and an SR830 TC choice (2026-10-14)

- **Label (the user's request):** the Zurich MFLI row in "PPMS external
  instruments" said "Time constant s"; it now says **TC (s)**, as RT-E's row
  does. The row only gets narrower. The Zurich TC value and its default are
  not part of this change.
- **SR830 TC (the user's request):** the PPMS page had no control for the
  SR830 time constant: Connect always set OFLT 10 (1 s) and OFSL 1
  (12 dB/oct) on each SR830. There is now one **SR830 TC** dropdown for all
  three SR830s, beside the **Macro** button in the free cell under the
  Keithley 2400 (left of SR830-3). The cell is set by the wide Keithley rows
  above it, so the dropdown takes no extra width in the 1101 px window the
  program opens with or in the 2000 px window.
  - **Choices:** the STFMR page's list and OFLT codes
    (`STFMR_LOCKIN_SR830_TIME_CONSTANTS`): 1 ms to 10 s, OFLT 4 to 12. The
    default is **1 s** (OFLT 10, `PPMS_SR830_DEFAULT_TIME_CONSTANT`), which
    is what Connect always set before. The slope stays 12 dB/oct.
  - **Connect** sends the chosen code where it always sent OFLT 10. The
    command order is otherwise unchanged. It reads OFLT? back with the other
    settings. On a mismatch the SR830 is disconnected and the error is shown,
    as for any other setting. While a Connect runs, the dropdown is locked,
    because the Connect has already read the choice.
  - **A new choice** is sent to every connected SR830 on the PPMS worker
    (the same path as a harmonic change): an OFLT write checked with *ESR?,
    then an OFLT? read-back. An SR830 that is not connected gets the choice
    when it connects. A changed time constant restarts autoranging for that
    SR830: the low-signal count and the cool-down are reset, and idle
    autorange waits 5 new time constants before ranging, as it does after a
    range step. The range itself is kept. If an SR830 refuses the code, the
    others are still changed, the error names that SR830 (choosing SR830 TC
    again retries), and its time constant is recorded as unknown. The
    dropdown is locked during a scan, any other PPMS command or an SR830
    Connect. A change refused at such a time puts the dropdown back to the
    value the SR830s were last sent. An idle poll can run beside the change
    on another PPMS worker. It now checks the ready time, ranges and sets
    the new ready time while it holds that SR830's lock, and it never moves
    a ready time earlier. Before this, a poll that had waited for the lock
    could cut the 5-time-constant wait short.
  - **Only a pick applies:** Tk's combobox steps to the next value when the
    mouse wheel turns over it and reports that as a selection. One notch
    would have rewritten OFLT on every connected SR830. The dropdown ignores
    the wheel (`<MouseWheel>`, and `<Button-4>`/`<Button-5>` on Linux).
  - **Autorange limit in a scan point:** each range step waits 5 time
    constants (read with OFLT?), and a point used to allow 180 s of ranging.
    That limit was set for 1 s, which is 5 s per step and room for 36
    steps. At 10 s it held only three 50 s steps, so the first point after
    Connect (SENS 9, 2 uV) stopped the scan for any signal above about
    20 uV. At 3 s, anything needing a range above about 10 mV failed. The
    limit is now 180 s or 36 step waits, whichever is longer
    (`PPMS_SR830_RANGE_LIMIT_S`, `PPMS_SR830_RANGE_LIMIT_STEPS`): 540 s at
    3 s and 1800 s at 10 s. At 1 s and below it is exactly 180 s, as
    before. The error names the limit used.
  - **Long time constants need a longer Settle s** (superseded the same day:
    every reading now waits at least 7 time constants and the note is gone;
    see "RT-E and PPMS: lock-in readings settle at least 7 x TC" above): a point is read Settle s
    after its field, temperature or angle is reached, and the scan does not
    wait for the time constant. An SR830 output needs about 5 time
    constants to settle. With 3 s or 10 s and the default 0.5 s, X and Y
    lag the scan by several points, and the first point's two agreeing
    windows can still be settling. For settled readings, set Settle s to at
    least 15 s (3 s) or 50 s (10 s). The scan-start confirmation (the scan
    time estimate) says so when the choice is longer than 1 s, Settle s is
    shorter than 5 of its time constants and an SR830 is measured. It is a
    note in that box, not a new dialog, so a Macro SCAN line shows no extra
    box. There is no note at 1 s, which is how the page always ran with its
    0.5 s settle.
  - **CSV:** each scan row gets one new column per SR830, added last:
    `sr830_<address>_time_constant_s`. Every scan start (and Set I) reads
    each measured SR830's OFLT? beside its HARM?/FMOD?/RSLP? reads, so the
    column holds the time constant in use, also after a change on the
    front panel or a power cycle. That read writes nothing, so the command
    order is unchanged. If it differs from the value last set, the log says
    so. A failed or invalid reply stops the scan start before the 6221
    turns on, as a HARM? mismatch does. All earlier columns keep their
    places.
  - **RT-E** shares the SR830 set-up code but builds its own rack. It has no
    such control and still sends OFLT 10 at Connect. It has no time-constant
    column and skips the scan-start OFLT? read (superseded the same day: it
    reads it too, for the 7 x TC wait; see "RT-E and PPMS: lock-in readings
    settle at least 7 x TC" above). The longer autorange limit does not
    change it, because at 1 s the limit is still 180 s.
- **Tests:** `test_ppms_sr830_tc.py` builds the real PPMS and RT-E pages on
  stand-in widgets, with fake SR830s, and checks:
  - the "TC (s)" label on both pages;
  - the dropdown's default, choices, state and width, and its place beside
    Macro under the 2400, left of SR830-3;
  - that Connect sends each choice in the old command order and reads it
    back, and that a mismatch is refused;
  - the lock during a Connect;
  - that a change is sent to every connected SR830 on the worker, never
    from the Tk thread, with the autorange reset, and that the next Connect
    sends the new choice;
  - the refusal and the lock while busy, and a failed read-back on one
    SR830;
  - that RT-E has no control and sends OFLT 10;
  - the new last CSV columns, through the real scan worker;
  - the autorange limit, on a clock that moves only when the scan waits:
    still 180 s at 1 s and 100 ms, 540 s at 3 s and 1800 s at 10 s; and the
    review's case through the real reading chain, where a fake SR830 at
    OFLT 12 overloads from SENS 9 up to SENS 17 and the point completes
    after eight 50 s steps;
  - an idle poll that waited for the SR830 lock while a change held it: it
    does not range and keeps the 50 s wait; a ready time is never moved
    earlier; and without a change the poll still counts low readings;
  - the scan-start OFLT? read: one query, no new write, before the 6221
    starts; a front-panel value is recorded and logged; an invalid reply
    stops the start; RT-E does not read it (since the 7 x TC rule it does:
    `test_rte_reads_it_too`);
  - the note in the scan time estimate, and no note at 1 s, with Settle s
    long enough, with only the Zurich, or on RT-E (replaced the same day by
    `StartSettleTests`: the confirmation shows the wait);
  - that the wheel bindings return "break".

  `test_sr830_filters.py` now expects `("OFLT", time_constant_code)` with a
  default of 10. A new CI GUI test, `test_panel_defaults.TCRackTests`,
  checks the label, and checks the dropdown beside Macro, left of SR830-3
  and no wider than the Keithley row, in both windows. It also checks that
  the dropdown's own wheel bindings run before the combobox class's. It
  generates no wheel event, because Tk may route one through the focus
  window. Its name sorts last in the module, because unittest runs a module's
  classes in name order. This class maps and then destroys a whole program
  copy. Before `PanelDefaultTests`' message boxes, that order crashed Tk's
  focus_force on the Linux CI. It was not run here because it needs a
  display.

## STFMR: default modulation frequency 117.3 Hz (2026-10-14)

- **The user's choice:** "Mod. start Hz" and "Mod. end Hz" now both start at
  117.3 Hz (`STFMR_LOCKIN_DEFAULT_LFO_HZ`; it was 317.3 Hz), so a default
  sweep modulates at 117.3 Hz. The idle Ref lamp falls back to 117.3 Hz when
  Mod. start is not a number. Only the STFMR page uses this value; the PPMS
  page's own 317.3 Hz is unchanged.
- **Tests:** `test_stfmr_mod_prime_range` and
  `test/test_instrument_safety_logic` check the constant and both boxes;
  the CI GUI test `test_stfmr_field_output` checks the boxes show 117.3.

## RT-E: Angle scan with the Newmark NSC-A1 rotation stage (2026-10-13)

- **What the user chose:** a third RT-E scan mode, "Angle", beside Field and
  Current. The Newmark RM-5-110 stage on its NSC-A1 controller turns the
  SAMPLE HOLDER, wires attached, inside the RT-E magnet; the field is fixed
  during the scan; the lock-ins ticked to Measure (SR830s and/or the
  Zurich) are read at each angle, as in the other RT-E scans.
- **Driver:** `NewmarkNSCA1Controller` in `Transport_all_NH_dev.py` (the
  program stays one file) is the reviewed core of `Newmark_motor_test.py`
  (999c1e2 + 164812f; this branch is cut from 164812f, with main's 15b88a1
  stop check and error-126 text ported by hand, 2026-10-13):
  `PerformaxCom.dll` over USB (never a COM port);
  "auto" takes the only Performax device, else the USB serial typed in its
  box; `ID` must name an ACE-SDE (and not be a device name like SDE01), then
  `VER`; the same allowlist without HL+/HL− (no homing in the program),
  number ranges included; a failed command is never repeated, and replies
  are brought back in step (flush, `ID`, `VER`) before the next one is
  trusted; the scale from `RR` → `R2` → `DRVMS` (steps/° = 40 × DRVMS);
  every move sets and reads back HSPD/LSPD/ACC (5 °/s), is absolute (`ABS`,
  `MM` read back, `X<steps>`), is refused while moving, with a latched limit
  error, a limit input on (the RM-5-110 has no limit switches) or the
  driver off (EO=0), ends after three "not moving" reads in a row and must
  read back PX exactly at the target; a time-out (3 × the expected time +
  5 s), a lost reply during the move, or a failed reply to `X` itself (the
  move may have started) sends STOP (ABORT if STOP does not stop it), for a
  move by hand as for a scan's. A stop counts as done only after three
  "not moving" reads in a row with PX unchanged: a STOP that lands just
  after `X` was answered, before the moving bits are set, does nothing, and
  one read would have called the stage stopped at its start while it went
  on to its target unwatched. A scale
  that cannot be read (SL/EDIO on, moving) leaves the stage connected, so
  Stop works, with moves refused until it is read.
- **One thread:** SiUSBXp.dll keeps its handles per thread, so every DLL
  call (connect, the idle read, moves, STOP, Set 0, disconnect, the close
  clean-up) runs on the RT-E page's single worker `rte-control`, never on
  the Tk thread; the controller refuses a call from any other thread before
  it reaches the DLL.
- **Controls (in the RT-E scan box since 2026-10-14):** a block of three
  rows level with the scan rows, right of them in the 1101 px window and
  under them when the scan box is beside the settings (the 2000 px window):
  "Stage" [address box, "auto"] **Connect** **Disconnect** lamp;
  "Stage °" [box] **Move stage** **Stop stage**; **Set stage 0°** and the
  stage line. "auto" takes the only Performax USB device; with several, its
  USB serial goes in the box. The stage line is the stage's one text: "Stage:
  not connected", the reading, e.g. "Stage: +12.345° (10000 steps/°)", ",
  moving", a limit warning or a read failure (read, PX and MST only, by the
  idle poll when the rack is idle), "Stage: stopping…" and the other
  messages below; a fixed box, so a longer message (a failure, an
  unconfirmed STOP) wraps inside it. The lamp follows the USB handle: a
  Connect whose first read fails still lights it, with "Stage read failed:
  …"; a Disconnect that closed the handle but could not confirm the stop
  turns it off, says "Stage: closed, STOP not confirmed", and shows the
  error (CHECK THE STAGE). The address box, Connect and Disconnect are
  locked like the rack rows'. **Move stage** (needs the safety
  tick and an idle rack; the target within ±360° of the stage zero),
  **Stop stage** (usable whenever the stage is connected, even during a
  move or a scan: it sets the stop event the move loop watches, and that
  loop sends STOP from the worker, and an Angle scan makes no further move;
  with no move or scan running it also queues STOP on the worker, and its
  result goes to the log; a STOP that could not be sent or confirmed, even
  after ABORT, also shows an error dialog (CHECK THE STAGE), because the
  next idle read replaces the stage line at once) and **Set stage 0°** (idle only; asks Yes/No first:
  the ±360° wire limit moves with the zero; PX=0 is lost when the NSC-A1 is
  powered off). Move and Set 0 are locked while the rack is busy, like the
  other edit widgets. A move by hand also stops (STOP, "Stage: stopped at
  … (OFF)") when **KEPCO Zero / OFF** or **6221 / 2400 OFF** is pressed:
  their OFF is queued on the same worker, and runs right after the stop
  instead of after the whole turn (up to 144 s for 720°). The scan mode
  list offers Field, Current, Angle;
  Angle's defaults are Start 0, End 360, Step 5 (units "Stage °"), Round
  trip ticked (back toward the start, unwinding the wires).
- **The Angle scan:** Start needs the stage (with its scale read), the
  KEPCO and the LS475 connected, every angle within ±360° of the stage zero,
  and the "KEPCO current A" and "Voltage limit V" boxes valid, and asks
  Yes/No first (the angle range, the points, the field current and that the
  stage turns the sample holder with its wires). At its first point it sets
  the KEPCO once (`set_output('CURR', current, limit)`), settles the field
  on the LS475 and holds it. Then at each angle: an absolute move (PX read
  back equal to the target; the stage line then shows where it stopped, not
  the last ", moving" update, which may come during the deceleration),
  Settle s, the field read, the acquisition (the
  first point by the same first-point check as the other modes), the field
  read again with the Field scans' drift check. Rows add
  `stage_target_deg`, `stage_angle_deg`, `stage_position_steps` and
  `stage_steps_per_degree` after `measured_field_g`, with the KEPCO's
  programmed current and voltage limit; Field and Current rows and CSV
  columns are unchanged. Harmonic passes repeat the angle list without
  setting the field again. The plots' x axis is "Sample angle (°)".
  The status line at Start is e.g. "RT-E Angle 0° to 360°, KEPCO +1.5 A
  held; 145 points" (the Yes/No already said that the stage turns the
  sample and its wires; with that said again the line did not fit the
  label on the CI). "Completed" and "Stopped" status lines end with where
  the stage is; after a failure the status line keeps only the failure, and
  where the stage is ("stage stopped at …" or "stage NOT confirmed
  stopped: CHECK THE STAGE") goes to the error dialog and the log.
- **Safety:** the ±360° wire limit is checked at Start, at Move, in the
  driver and by the allowlist; moves are absolute, so 350 → 0 turns back
  −350°, never across 360. Stop (the scan's) or Stop stage during a move:
  STOP, wait for it, the scan ends as Stopped with the outputs held. Any
  failure switches the 6221 off; a field failure (settle, drift or read)
  also switches the KEPCO off, as in Field scans; a stage failure sends
  STOP (unless the driver's own STOP already ran) and keeps the KEPCO's
  field. No scan of any mode starts while the stage's last stop was not
  confirmed ("press Stop stage first"): no scan watches the stage, the idle
  poll pauses during a scan, and Stop stage queues no STOP behind a running
  scan (review, 2026-10-14). The 6221 is switched off after the stage's
  STOP, not before it: the STOP is slow (up to about 15 s) only on a USB
  link that has failed, where whether the stage turns is unknown, and a
  move refused because the stage is moving has the same path.
  Closing the program sets both stop events; the queued clean-up
  disconnects the stage first (STOP if it is moving, then Close, on the
  worker), then the other instruments as before (the KEPCO and 6221 close
  as they did).
- **DLL setup on the lab PC (64-bit Python 3.11):** as for
  `Newmark_motor_test.py`: the Performax USB driver, and the 64-bit
  `PerformaxCom.dll` and `SiUSBXp.dll` (Arcus "USB 64-bit DLL v2.06")
  together in the program's folder, beside `Transport_all_NH_dev.py` (else
  the normal Windows DLL search, which in Python 3.8+ does not include
  PATH). A 32-bit DLL (WinError 193), a missing DLL or SiUSBXp.dll (error
  126, which Python 3.8+ raises as FileNotFoundError "Could not find module
  …") and a 32-bit Python are reported in plain words. Close QuickMotion
  and `Newmark_motor_test.py` first: one program at a time on the USB
  device. Try the stage with `Newmark_motor_test.py`
  first (direction of +, the scale), set the zero where the wires hang free,
  and only then scan.
- **Window height (user/CI, 2026-10-14):** the window's height is fixed.
  The first layout (a rack row "Newmark stage" and a row "Stage °" under
  the RT-E settings) took about 84 px of height in the 1101 px window the
  program opens with, and on the CI screenshot `RT-E_1101px.png` (1101 ×
  1300 px) every plot fell below its 55 px minimum ("Increase the window
  height"). Now the block uses the empty width right of the scan rows
  there (estimated from the Windows CI screenshots, Microsoft YaHei UI
  11 pt: rows 539 px + gap 16 + block 441 = 996 of the box's 1069 px;
  still to be checked on the next CI screenshot) and is no taller than the rows
  (36 + 36 + 34 px against 36 + 36 + 42: its buttons have the style
  `Inline.TButton`, as tall as an entry), so the plots have the height
  they had before the stage (69 px canvases at 1101 × 1300 px on the
  screenshot of d6674c9). The one exception is a message longer than the
  stage line's fixed box (32 digits wide, about 288 px on Windows), such
  as "Stage: STOP not confirmed: CHECK THE STAGE", a reading with
  ", moving" or ", limit input on", or "Stage read failed: …" (its error
  cut to 60 characters): it wraps onto two or three lines rather than
  being cut, so a warning is read whole, and while it shows the block, and
  the scan box with it, is up to about 22 px taller than the rows. The
  plots then have about 62 px (estimated; CI checks that they keep their
  55 px minimum on Windows) and get their height back with the next
  one-line message. The former rack line did the same. In the 2000 px
  window the scan box is beside the settings and too narrow, so the block goes under the scan rows (the scan
  box grows by about 110 px there, where the plots have height to spare,
  and the rack is one row shorter). The
  settings/scan pair decides by the scan box's width with the block under
  the rows, so the pair inside the scan box cannot change that decision
  (nested pairs once looped at start-up) and the layout does not depend on
  how the window was resized.
- **Not yet tried on the hardware.**
- **Tests:** `test_rte_angle_scan.py` (Tk-free; the fake DLL is
  `Newmark_motor_test.py`'s simulated controller behind the same ctypes
  calls, recording each call's thread; `ctypes.WinDLL`,
  `os.add_dll_directory`, the program's loader, `tkinter.Tk` and every
  dialog fail a test that reaches them; a virtual clock). The driver: the
  device choice, the ID check, the allowlist, the scale (and one that cannot
  be read), absolute moves with the speeds read back, the idle reads, the
  refusals, the wire limit, Stop during a move, a Stop in the gap right
  after `X` (main's StopLostInTheGap simulator: reported where it really
  stopped, after three still reads), a lost reply to `X` (STOP, also when
  it cannot be confirmed), the time-out, PX off the
  target, a lost reply (not repeated, then resynced), Set 0, disconnect
  stopping a turning stage first, a call from another thread, an old DLL
  without flush, the loader's errors (error 126 as FileNotFoundError
  too). Thread affinity with a real
  single-worker executor: connect, the idle read, Move, Set 0, Stop, an
  Angle scan, disconnect and the close clean-up, with no DLL call on the
  test's thread. `rte_scan_plan` for Angle. The scan worker end to end
  (rows and columns, the KEPCO set once, harmonic passes, Stop during a
  move, a Stop in the gap after `X`, Stop stage between moves, the stage
  line at each move's end before Settle s, a refused
  move, PX off the target, a time-out and a lost reply to `X` keep the
  KEPCO and stop the 6221 with STOP sent once, an unconfirmed stop logged,
  drift, settle and read failures switch the KEPCO off). Move, Stop and
  Set 0 by hand (refusals, Yes/No, Stop from the Tk thread while a move
  runs on the worker, Stop stage when idle logged, and one that cannot be
  confirmed or sent logged with a dialog, a lost reply to `X` stopped, KEPCO Zero / OFF and
  6221 / 2400 OFF during a 72 s move: STOP first and their OFF within a
  poll and a stop); the stage line after a failed first read and after a
  Disconnect that closed without a confirmed stop; Start (refusals, every
  mode refused while the stage's last stop is unconfirmed, the
  confirmation, the submitted scan, the status lines, the stage line in a
  failure's dialog and log); the
  defaults, the plot axis and the page build on stand-in widgets (the
  block's three rows in the scan box, none in the rack or the settings, one
  stage line, the button states).
  `test_rte_panel.py`'s removed-mode plan check now refuses an Angle range
  beyond ±360° instead of every Angle plan; the other Field and Current
  tests are unchanged; its page build finds the stage's connection in the
  scan box. `test_panel_defaults.RTEStatusRowTests` (CI only:
  it needs a display) also checks that the longest Angle status lines fit on
  one line. `test_pair_layout_loop.RTEStageBlockTests` (CI only): the block
  beside the scan rows at 1101 px and under them at 2000 px whatever the
  resize history, no taller than the rows, a reading on one line, all
  nine plots at least 55 px tall at 1101 × 1300 px, and a long message
  wrapped inside the stage line (the block keeps its width and the pairs
  their layout; on Windows the plots keep 55 px while it shows).

## STFMR: Mod. start / Mod. end, prime modulation frequencies (2026-10-13)

- **User:** "add a Mod. start & end input like f and A in the STFMR module.
  also only pick the prime, ignore the step input."
- **Boxes:** the single "Mod. Hz" box is now "Mod. start Hz" (its old cell,
  at the end of the f row) and "Mod. end Hz" under it (end of the A row);
  both default to 317.3 (117.3 since 2026-10-14, see above). There is no step
  box. "Wait/pt s" moves from under Mod. to the free cell right of Loop
  (SR830 row). Each row keeps five pairs: a sixth made the table too wide
  for the 1101 px window. The label column is wider for "Mod. start Hz:"
  (about 33 px in Microsoft YaHei UI, the font the program picks on the lab
  PC). In the 1101 px window on Windows the table was already about 17 px
  too wide, so number columns 5 and 9 shrink: the f step, Step A, Mod.
  start, Mod. end and Wait/pt boxes are about 64 px wide (about five digits
  showing), with about 5 px left before the table runs past its box
  (review, measured from the Windows CI screenshot). Watch the Windows CI
  GUI run, not the Linux one (about 20 px to spare there).
- **Which frequencies:** Mod. start = Mod. end gives that one value (any
  finite positive number, e.g. 317.3). Otherwise every whole-number prime
  between them, both included, from Mod. start towards Mod. end (descending
  when start > end), as the f and A ranges run from start to stop. Start
  refuses, with a message and before any output or file: a box that is not
  a finite positive number, a range with no prime, more than 10000 primes
  (the microwave sweep's own limit), or a prime range above 2^53 Hz.
- **The sweep:** the whole f × A sweep (every carrier, every loop) runs once
  per modulation frequency, modulation outermost, so each modulation gives
  a complete sweep and the LFO changes as few times as possible. At each
  change: the 4886 to zero (as at a carrier change), the SMB100A
  `LFO:FREQ` only (the command the start sends; RF, AM and LFO stay on),
  the carrier back to f start, the SR830 reference wait of the start (5 s,
  at the new frequency, and its readback must also be nearer the new
  frequency than the old one: neighbouring primes less than 0.5 % apart,
  such as 1019 and 1021 Hz, both pass the 0.5 % match, and the first poll
  comes right after `LFO:FREQ`; the MFLI follows its external reference by
  itself, as at the start, and each reading checks the frequency), then the 4886
  back on at Start A, the start pre-read and the first two readings
  discarded, as after a carrier change. Every read expects the modulation
  frequency in use. A failed LFO change or a lost lock stops the sweep with
  the 4886 at zero and the RF off.
- **Records:** each live CSV row already had `modulation_frequency_hz`; it
  now holds that row's modulation frequency, so no column was added and
  every column keeps its place. `index` runs across the whole run;
  `frequency_index` counts the carriers within each modulation frequency.
  The log's settings add `modulation_frequencies_hz`, `modulation_start_hz`
  and `modulation_end_hz` (`modulation_frequency_hz` is the first one); the
  `frequency_configure`, `frequency_ready`, `startup_point_discarded` and
  `frequency_complete` events name the modulation frequency, and
  `frequency_configure` says whether it changed (`modulation_change`).
- **Start confirmation and estimate:** a line such as "Mod. Hz: 307, 311,
  313, 317 (4 primes from 300 to 330); the whole f × A sweep runs at each,
  in this order" ("Mod. Hz: 317.3" for one value; a long list shows its
  first five and last two). The saved-point count, the duration estimate
  and the progress bar count every modulation frequency.
- **Plots and status:** with several modulation frequencies each curve is
  named "5 GHz, 307 Hz" and has its own shade, in the live plots and in
  the PNGs of Save CSV; with one, names and colours are as before. The
  PNG legend uses fewer columns (e.g. 4 instead of 5 on one meter) when its
  widest label would run into the next entry; carrier-only legends keep 5
  (3 in dual mode). The live plots' legend is one line, so with many
  carrier and modulation pairs the later entries fall past the plot's right
  edge, as many carriers already did; the PNGs show every entry. Status
  lines name the modulation briefly, so they fit the 52-character status
  label: "5 GHz/311 Hz; scanning 123/480; I=+0.5 A; ~120 s left", "5 GHz/311
  Hz: start I=-3 A sent; start pre-read now", "Mod. 307 Hz done; zeroing;
  LFO to 311 Hz at 5 GHz". The end status counts the modulation
  frequencies; a one-frequency sweep's lines are as before.
- **Idle Ref lamp:** compares the lock-in's reference with Mod. start (the
  one value when start = end).
- **Docs:** README_STFMR_DUAL.md says how the modulation frequencies run and
  what the CSV column holds.
- **Tests:** `test_stfmr_mod_prime_range.py` (the list: equal boxes keep a
  non-integer value, ascending, descending, both bounds included, the same
  primes as the Non-local sieve, no prime, bad numbers, the 10000 cap and
  the 2^53 bound; the confirmation line; the SMB100A sends only
  `LFO:FREQ`; real sweeps on the STFMR harness over three primes and two
  carriers with the SR830, a descending dual-meter sweep and an MFLI sweep:
  the LFO set per modulation in order, the 4886 zeroed before and switched
  on after the lock re-check, every read at its pass's frequency, the
  settling readings discarded after each change, rows tagged, the CSV
  header unchanged, the confirmation, the estimate, the log, separate
  plot curves; a lost lock, a failed LFO change and Stop end with the
  outputs off; the default 317.3 sweep unchanged; refusals before any
  output or file; the idle Ref lamp; the table cells from the real page
  build). The harnesses in `test/test_stfmr_realtime_recording.py`,
  `test/test_instrument_safety_logic.py`, `test_fmr_stfmr_minors.py` and
  `test_fmr_stfmr_review.py` set the two boxes instead of the old one. The
  CI GUI test `test_stfmr_field_output.py` gains
  `test_mod_start_and_end_take_the_last_column_and_fit` (the three cells,
  defaults 317.3, nothing past the edge or cut in the 1101 px and 2000 px
  windows, and also against the parameter table's own edge, which hides
  what runs past it 17 px inside the tab's; not run here: it needs a
  display). Every dialog is mocked and a real Tk root fails the new tests.
- **Review fixes (2026-10-13), tested in `test_stfmr_mod_prime_range.py`:**
  the real SR830 `wait_for_reference` on a fake instrument (1019 Hz is not
  taken for 1021 Hz, nor the midpoint; descending; no previous frequency
  as before; an SR830 left on the old frequency is an error, and in a sweep
  it stops with the 4886 at zero and the RF off); the status lines of a
  sweep over 1009 and 1013 Hz, with thousands of seconds left, fit 52
  widths of "0" (measured in Arial 15 px, a little wider than YaHei UI);
  the real PNG
  exporter's legend labels never reach the next entry or the plot edge
  (one meter, MFLI and dual) and carrier-only legends keep 5 / 3 columns;
  the prime test's docstring now says exact below 3.18e23 (the first
  composite passing the 12 bases), far above the 2^53 cap.

## MOKE-24: OVP margin warning, Zero on an output that is on (2026-10-13)

- **Scan near the OVP (the user's choice: warn only):** the MOKE-24
  software limit equals the hardware OVP (200 V on a 2410, 160 V on a
  2400), and the OVP levels have a ±5 % tolerance, so a scan allowed near
  the limit could be clipped by the OVP. Now, when the scan's largest |V|
  is above 95 % of the connected model's OVP (190 V / 152 V), the Start
  confirmation adds a "Near the hardware OVP" paragraph (below the
  "High-voltage scan" note, which such a scan always gets): the |V|
  reached, the margin to the OVP in V and %, and that the source may clip
  the top of the scan. The OVP, the software limit and the scan are
  unchanged; Yes still starts it.
- **Zero on an output that is on (the user's choice: 0 V only):** Zero
  went through `apply_manual_voltage(0.0)`; on the 1000 V range (2410) or
  200 V range (2400) that meant a range change, so it sent :OUTP OFF and
  then :OUTP ON at 0 V without the safety tick. Now Zero on an output that
  is on writes :SOUR:VOLT 0 (error-checked), reads :SOUR:VOLT? back, and
  keeps the range and the output on; no :OUTP command. The page shows
  "Output: +0 V / ON" and "Zero: … read back; the output stays on, range
  unchanged". A failed write, a failed or non-zero readback, or an output
  not in voltage-source mode (nothing sent) is reported as "Manual voltage
  failed" and the page keeps its previous voltage. Zero on an output that
  is off is unchanged (0 V and :OUTP OFF, never :OUTP ON).
- **Tests:** `test_moke24_ovp_margin_and_zero.py` (the real Start, submit,
  finish and red-button methods and the real controller with a stand-in
  Keithley: the warning text and margin on a 2410 at 195 and 200 V (below
  the high-voltage note, above the scan summary), a negative scan and a
  2400 at 156 V; none at 190 V / 152 V, the warning at 191 V / 153 V; Yes
  goes on past the confirmation; Zero on the 1000 V and 20 V ranges sends
  only :SOUR:VOLT 0 and keeps range and output; a timed-out write, a SCPI
  error, a failed, wrong, NaN or unparsable readback and the current-source
  mode are never shown as 0 V, and a retry still sends only :SOUR:VOLT 0;
  after the red button Zero keeps the output off). In
  `test_moke24_zero_stays_off.py` the Zero-while-on test no longer says
  "as before": it checks that only :SOUR:VOLT 0 is sent and the range is
  kept. Every dialog is mocked, and a real Tk root fails the test instead
  of opening a window.

## STFMR: SR830 time constant choice, fixed-current runs (2026-10-13)

- **SR830 time constant:** a sweep set the SR830 to a fixed 1 s (OFLT 10,
  12 dB/oct) while the default Wait/pt is 0.5 s, and the page had no
  control for it, so each SR830 point was a lagged reading. The user chose an
  "SR830 TC" dropdown (1 ms to 10 s, OFLT codes 4 to 12) in the MFLI TC row,
  in the free cell under Loop and as wide as the Loop spinbox, so the table
  keeps its width in the 1101 px window; its column gives way in a narrower
  window only down to the dropdown's width. The default is 100 ms (user,
  2026-10-13; the fixed value had been 1 s; superseded on 2026-10-14: the
  default is 1 s again, see the top section), still at 12 dB/oct, with an
  OFLT? read-back, as for SENS, that stops the sweep with the outputs off on
  a mismatch. As for the MFLI TC, Start reads it only when that lock-in is
  used; a value not in the list is refused, and the dropdown is locked during
  a sweep.
- **SR830 wait:** whenever the SR830 is read (SR830 alone or MFLI + SR830),
  each point and each re-read after a range change waits the longer of
  Wait/pt and 5 × TC (12 dB/oct settling). With the default 100 ms that is
  the default 0.5 s Wait/pt; at 1 s (the default since 2026-10-14) an SR830
  point waits 5 s. The Start confirmation
  shows the TC and the wait, and the duration estimate uses the wait. The run
  log records `sr830_time_constant_index`, `sr830_time_constant_s` and
  `effective_point_wait_s`; the live CSV gains a last column,
  `sr830_time_constant_s` (SR830 rows; `nan` on MFLI rows). The MFLI alone
  is unchanged, and the start pre-read and the first point of each frequency
  are still read at once (both are discarded).
- **Fixed-current runs:** the first two points of every loop were discarded
  (since ca5ee54: each loop starts with the jump from Stop A back to
  Start A), so a fixed current (Start A = Stop A, one point per loop) saved
  nothing and Start refused it (0e82358). The user chose to allow such runs.
  Now only reads after a current jump are discarded: the first two of each
  frequency (the 4886 has just gone from 0 to Start A, with a new carrier)
  and the first two of each later loop of a sweep. A fixed current has no
  jump between loops, so Loop is the number of readings at each frequency and
  all but the first two are saved. Start refuses only a run that would save
  nothing (two currents per loop, or a fixed current with Loop 1 or 2) and
  says why; sweeps of 3 or more currents discard as before. The Start
  confirmation and the saved-point estimate follow the rule, a fixed
  current's discarded-point status speaks of readings (a sweep's is
  unchanged, loop 2's still showing 6/2 and 7/2), and the plot legend names
  each frequency on its first saved loop (a fixed-current run starts at
  loop 3, so its legend was empty).
- **Docs:** README_STFMR_DUAL.md: the discard sentence, the SR830 wait (but
  for the discarded first point of each frequency) and the new CSV column.
- **Tests:** `test_stfmr_sr830_tc_fixed_current.py` (the choices against
  the SR830's OFLT table; the set-up's commands in the old order with the
  chosen code, the OFLT read-back and a refused code; the waits of SR830,
  dual and MFLI sweeps and of both kinds of re-read, the confirmation, the
  log and the CSV column, all through the real sweep code on the STFMR
  harness; the lock; the cell under Loop and its column floor, from the real
  page build on stand-in widgets; the discard rule, a fixed-current run end
  to end, alone and in dual mode, with the 4886 commands and statuses; a
  sweep's discards and unchanged status; the refusals before any output or
  file; the legend).
  `test_fmr_stfmr_minors.ShortLoopTests` now expects a fixed current to
  start. The harness in `test/test_stfmr_realtime_recording.py` has the
  SR830 TC box, and the fake SR830s in
  `test/test_instrument_safety_logic.py` answer `OFLT?`. The CI GUI test
  `test_stfmr_field_output.py` checks that the dropdown sits under Loop and
  shows every choice in the 1101 px and wide windows and is whole in the
  narrowest (not run here: it needs a display). Every dialog is mocked and a
  real Tk root fails the new tests.

## STFMR: Save CSV also writes the PNG figures (2026-10-13)

- **What was wrong:** the STFMR lock-in page's "Save CSV" wrote the CSV
  only; the 900 DPI figure exporter (`_export_stfmr_lockin_data_pngs`) and
  the screen-snapshot one (`_save_stfmr_lockin_plot_snapshots`) were never
  called, so no PNG was written anywhere.
- **What the user chose:** Save CSV also writes the PNGs; the end of a sweep
  stays CSV only.
- **The change:** after the CSV is written, Save CSV draws four figures next
  to it, `STFMR_field_scan_<time>_X / _Y / _theta / _field_vs_current.png`,
  with the data exporter: it draws every saved row (both meters' curves in
  MFLI + SR830) at 900 DPI whether or not the plots are on screen, so the
  PNGs show what the CSV holds. The snapshot one would grab the screen and
  fails when a plot is hidden; it stays unused. The status line shows
  "Saving PNG… (CSV saved: <csv>)" (redrawn before the figures are drawn on
  the Tk thread, about a second; Stop waits that long if a sweep is
  running), then "CSV + 4 PNG saved: <csv>". The news comes before the CSV
  name because the status label is a fixed 52 characters wide and the name
  alone fills most of it; only the end of the name may be cut off. A PNG
  failure keeps the CSV and is reported on its own: the status line reads
  "CSV saved; PNG not saved: <error, cut to one short line>" and a "Save STFMR
  PNG" warning names the CSV and gives the full PNG error. The folder
  dialog's title now says CSV and PNG figures; the exporter's style and the
  button label are unchanged. README_STFMR_DUAL.md says so.
- **Tests:** `test_stfmr_png_save.py` (the real Save CSV and the real
  exporter on dual-meter rows: the CSV is complete and the status says
  Saving PNG… before the first figure, four 900 DPI PNGs named after the
  CSV; a PNG failure, with a two-line error, keeps all CSV rows, no error
  dialog, one warning naming the CSV and the full error, a one-line status;
  each status has its news within its first 40 characters, so the 52-wide
  label shows it; a CSV that cannot be written shows the error
  and draws no PNG). `test/test_stfmr_realtime_recording.py`
  `test_manual_save_uses_data_export_without_imagegrab` now checks that the
  manual save calls the exporter once, after the CSV, without ImageGrab
  (it asserted no export); the sweep-end tests still assert no PNG.

## Newmark_motor_test.py: a standalone test of the NSC-A1 rotation stage (2026-10-13)

- **User:** an individual test script for the Newmark rotation stage that
  turns the sample holder (wires attached) inside the RT-E magnet: NSC-A1
  controller, RM-5-110 stage (72:1 worm gear, home switch, no encoder, no
  limit switches), before an Angle scan on the RT-E page.
- **`Newmark_motor_test.py`** (repository root; standard library only; no
  COM port is opened): it talks to the NSC-A1 (an Arcus ACE-SDE) through
  the vendor's `PerformaxCom.dll` over USB, following the NSC-A1 manual
  rev 1.3, and prints every command and reply. Only a fixed list of commands
  can be sent: STORE, RW, J+, POL=, DRVMS= and the rest are refused before
  they reach the controller, so nothing is ever stored to flash; the numbers
  are checked too (X and PX= far inside the 28-bit counter, and X within
  ±360° once the scale is known; HSPD at most 20 deg/s; 10 ≤ LSPD < HSPD;
  1 ≤ ACC ≤ 5000 ms). Modes: no argument = read only (devices, ID/VER/DN,
  MST decoded, EO, PX, speeds, POL/IERR/RZ/HCA/RT/SL/EDIO/TOC, driver
  parameters via RR, steps/deg = 40 x DRVMS); `move --degrees D [--speed
  S]` (relative, |D| ≤ 360, default 5 deg/s, at most 20; sent as an
  absolute target, which must lie within ±360° of the zero for the sample
  wires, so 350 → 0 goes back −350; asks "yes"; it counts as ended after
  three status reads in a row say not moving, and PX must read back the
  target exactly); `stop`; `zero` (PX=0, asks, since every move's ±360°
  limit counts from it; runtime only: lost when the controller is powered
  off); `clear` (CLR); `enable` (EO=1, asks) / `disable` (EO=0);
  `home --direction +` or `-` (HL+/HL−, asks; see below); `--serial` (only
  with several Performax devices), `--dll-dir` (a folder without
  `PerformaxCom.dll` is a usage error), `--yes`, `--dry-run` (a simulated
  controller whose stage starts at −20°; nothing sent). A move is refused
  while it moves, with a latched limit error, with a limit input on at rest
  (a polarity problem: fix it in QuickMotion), or with the driver off
  (EO=0). Ctrl+C at any point sends STOP if a move may be running, waits
  until it has stopped (ABORT if STOP does not stop it) and says where it
  stopped; while it stops and reports, a further Ctrl+C only asks for ABORT
  and cannot cut the report (or exit status 3) short. A second press a few
  ms after the first, before that hold is in place, leaves the stop to the
  closing step, which still sends it and reports it (exit 1 or 3, no
  traceback). A stop counts as confirmed after three status reads in a
  row say not moving with PX unchanged (one read could fall in the moment
  after a motion command's OK, where a STOP may not take).
- **`home`** (the RM-5-110 turns without end and has no limit switches, so
  a home switch that never triggers must not wind up the wires): PX is read
  at every poll. The controller sets the counter to 0 at the switch (manual,
  Figure 6.6); if that has not happened within one turn + 10°, the search is
  stopped (STOP) and reported as "no home switch within one turn"; after
  the switch it is stopped if it runs on more than the slowing down + HCA +
  10°; a time-out (1.2 × the expected time + 2 s) backs this up. It is
  refused unless all of that stays within ±360° of the zero: from the zero
  itself, first move about 13° the other way at 5 deg/s (the refusal prints
  the `move` to make; where that would be more than the 360° one `move`
  turns, it suggests the other direction after a short move). It must end
  not moving with PX 0. Afterwards the zero, and with it the ±360° limit of
  `move`, is the home switch; the script says where the switch was found
  from the old zero and the `move` that turns back there. A search stopped
  or failed after the counter reset (Ctrl+C, time-out, communication
  failure, the travel check, HL ending off 0) says the same, with the move
  back from where it stopped. A switch within about one poll of the old
  zero makes the reset look like a normal step, so then only a range is
  given ("within about 2.6° of the old zero" at 5 deg/s).
- **Setup on the lab PC (64-bit Python 3.11):**
  1. Install the Performax USB driver ("Performax USB Setup 4.01.3", from
     Newmark/Arcus). With the controller on its 24 V supply and plugged in,
     Device Manager should list it (expected `USB\VID_1589&PID_A101`; worth
     confirming).
  2. From Arcus's "USB 64-bit DLL v2.06", put the 64-bit `PerformaxCom.dll`
     and `SiUSBXp.dll` together next to `Newmark_motor_test.py` (or in a
     folder given with `--dll-dir`). 32-bit DLLs do not load in 64-bit Python
     (WinError 193); the script says so, and it refuses a 32-bit Python
     (use `py -3.11-64`). Python does not search PATH for them: a missing
     DLL or a missing `SiUSBXp.dll` beside it (error 126, which Python
     reports as "Could not find module") is explained as such.
  3. Close QuickMotion first: one program at a time on the USB device.
- **First test:**
  1. `py Newmark_motor_test.py --dry-run` and
     `py Newmark_motor_test.py move --degrees 10 --dry-run` (no hardware).
  2. `py Newmark_motor_test.py` (read only): ID "Ace-Series-SDE", MST not
     moving and no limit bits, EO 1, and the scale line (e.g. "10000 steps/deg
     (72:1 x 200 full steps x DRVMS 250 / 360)") against QuickMotion.
  3. With the sample wires hanging free: `py Newmark_motor_test.py zero`.
  4. `py Newmark_motor_test.py move --degrees 5 --speed 2`: watch the stage,
     note which way + turns (clockwise or counter-clockwise seen from above)
     and whether 5° looks right; then `move --degrees -5`.
  5. `move --degrees 30`, and Ctrl+C halfway: it should stop and say where.
  6. Only then, if wanted, back at the zero: `move --degrees -15`, then
     `home --direction +` (wires free for a full turn). If the home switch is
     not wired or its polarity is wrong, it stops by itself after one turn
     + 10° ("no home switch within one turn"). After a home search the zero
     is the home switch: if the wires do not hang free there, make the
     `move` it prints (back to the old zero) and run `zero` again.
- **Exit status:** 0 done; 1 refused, not confirmed, or failed with the
  motor confirmed stopped; 2 usage error (nothing sent); 3 motion state
  unknown (check the stage).
- **Not yet tried on the hardware.** Things to check: whether the DLL has
  `fnPerformaxComFlush`, the reply framing (CR, a `#NN` prefix), the STOP
  reply at rest, R2 = 1 after the 2.5 s wait following RR, DRVMS, the
  direction of +, and how HL behaves (the counter reset at the switch, PX 0
  at the end, the home polarity). Check in QuickMotion that EDEC = 0: the
  home bounds assume the slowing down takes ACC, but with EDEC = 1 (stored
  in flash) HL and STOP slow down over DEC, which the script does not read
  (reading EDEC and DEC would need adding them to the allowed list).
- **Tests:** `test_newmark_motor_test_script.py` (Tk-free; no real DLL is
  loaded on any OS, including the Windows CI: `ctypes.WinDLL`,
  `os.add_dll_directory` and the loader are replaced; time is a virtual
  clock that fails a test past 1000 virtual seconds, so a lost time-out
  fails instead of hanging; Ctrl+C goes through a stand-in signal module).
  Covered: every mode and `--dry-run`; usage errors (also a `--dll-dir`
  without the DLL) are exit 2 with nothing opened; the allowlist refuses
  STORE, RW, J+, DRVMS=, POL=, HL+ outside `home` (checked for every mode in
  `main`) and the rest, numbers out of range, and commands of 64 bytes or
  more; 0, 1 and 2 devices, `--serial`; ID first (another device gets only
  `ID`, and a DN-like "SDE01" is not an ID), VER answering like ID is out
  of step; info sends only read-only commands and RR (R2 at least 2 s after
  it); a failed transaction is followed by flush, ID and VER and never
  repeated, and a stale reply is never used (also without flush, also a
  late DN reply); the refusals (moving, latched limit error, limit input,
  EO=0, beyond ±360°, speeds not read back, a position changed since the
  question); one "not moving" read does not end a move; a PX read-back that
  differs; a time-out sends STOP, then ABORT, else exit 3; a USB failure
  mid-move or mid-search (STOP, back in step, exit 1; without flush, exit
  3); `zero` asks; `home` both ways, with HCA, the plan's numbers, refused
  where it could pass ±360° (its advice always a `move` that parses and
  makes the search fit, also from −355°), no home switch (stopped after
  one turn + 10°, both ways), running on after the switch, a pause between
  HL stages, HL ending off 0, a time-out, the old zero given after a Ctrl+C,
  a time-out or HL ending off 0 after the counter reset, a switch near the
  old zero (a range); Ctrl+C at the question, during RR, during and inside
  the move command, twice, before the identity, in info, home and stop,
  after a finished move, a second Ctrl+C while it reports or before ABORT
  (the report and exit 3 are kept), a second Ctrl+C before the handler or
  close holds it (no traceback; the stop on closing runs; exit 3 after a
  stop that ended unconfirmed), a STOP lost just after the move command
  (three still reads), and a stop on closing reported; the simulator's STOP
  reads "decelerating"; the ctypes path through real ctypes function
  pointers (argtypes, pointers, 64-byte NUL-terminated buffers, 256-byte
  product strings, timeouts before Open, flush after it, the same handle
  closed); the loader (search order, SiUSBXp.dll first, WinError 193
  explained without guessing it from the path, a missing DLL as the
  FileNotFoundError Python 3.8+ raises, 32-bit Python refused, missing
  functions); standard-library imports only.

## Settings audit: last review notes (2026-10-13)

- **LED boxes:** a number too large for an integer (e.g. "1e400") raised
  OverflowError, so the button again did nothing; it now shows Input error
  like an empty box.
- **PPMS Macro status:** a WAITFOR wait that is switched off showed "auto";
  it now shows "-" (only a TEMP without a rate shows "auto").
- **CIDWM 6221 scan Start:** the compliance was checked against the 6221's
  last read-back width, which could sit just under 5 us; it is now checked
  with the box's width only.
- **STFMR Disconnect:** the row says "Disconnecting…" until the disconnect
  ends, so a Read or Meter change meanwhile no longer enables Disconnect.
- **Docs:** README_STFMR_DUAL.md no longer mentions a PNG and says the first
  two points of each loop are discarded; the magnet section no longer says
  Zero both now ticks Continuous.
- **Tests:** `test_light_camera_inputs` ("1e400"),
  `test_ppms_rte_review.MacroTempKeepsItsRate` (WAITFOR),
  `test_k6221_failed_off_interlock.ScanWidthComplianceTests`,
  `test_fmr_stfmr_minors.ConnectDuringSweepTests` (Disconnecting…).

## LED, MOKE motor, camera rows: bad boxes, Pulse on one channel, step range, CIDWM status (2026-10-13)

- **LED, an empty or non-numeric box:** clearing a channel's Current (or
  p/g/d/f) or the "Continuous current, all 4" box, or typing 'abc', raised
  TclError outside the integer check, so Apply, Apply all, the "All 4
  currents" presets, "All 4 continuous on" and "Set continuous current on
  all 4" did nothing and showed nothing. Now the box is read inside the
  check: "Input error" names the box and nothing is sent.
- **LED, Pulse on one channel:** Apply all asked "Confirm pulse mode" (the
  p/g/d/f units are unconfirmed) but a channel's own Apply did not. Now it
  asks the same.
- **MOKE motor, Step message:** a Step of 0 (the default "0.000") is refused,
  but the message read "Step range: 0–20000 µm". It now reads "Step must be
  greater than 0 and at most 20000 µm". The default is unchanged.
- **MOKE motor, repeated steps:** Up/Down targets add up and were never
  checked against the ±100000 µm software range of Go to absolute position
  (after the 7250 µm preset, the 19th Down of 20000 µm passed it). Now a step
  whose target is beyond that range is refused with the same warning; the
  limit itself is reachable, as for an absolute move.
- **Camera settings rows on CIDWM:** averaging, binning, mean filter and
  Apply exposure wrote their status only to the MOKE magnet page's line.
  Now the CIDWM status line shows it too, as for the ROI live settings.
- **Tests:** `test_light_camera_inputs.py` (every LED button with an empty
  and an 'abc' box, through the real `IntVar.get` on a stand-in
  interpreter; valid boxes still apply; single-channel Pulse asks and No
  sends nothing; the Step messages; 18 Down steps from 7250 µm pass and the
  19th is refused with nothing written; 20 Up steps reach -100000 µm and the
  21st is refused; both status lines); `test_camera_settings_rows.py` page
  stand-in gains the CIDWM status line. Every dialog is mocked (the motor
  code imports `tkinter.messagebox` itself, so its dialogs are mocked
  there), and a real Tk root fails the test instead of opening a window.

## MOKE magnet: PNG of the saved scan, field-read recovery, Target after Stop, Zero presses (2026-10-13)

- **Save CSV + PNG saved the page's plot, not the scan:** the PNG was a
  screenshot of the page's plot, which is empty after Clear curve or an
  ROI nudge, shows the channel the selector is on now, and keeps only the
  last 2000 points, while the CSV held the whole scan. Now the plot draws
  the saved scan (its channel, every CSV row) for the snapshot, then goes
  back to the page's view (also when the snapshot fails). Before the grab
  it waits until that drawing is on the screen: on Windows the desktop
  compositor shows a redraw only at its next frame (~16 ms), so a grab
  straight after it could still save the page's plot (review); it now
  flushes GDI and waits with DwmFlush (or 50 ms if that is unavailable).
  Worth one check on the lab PC: Clear curve, then Save CSV + PNG.
- **One gaussmeter timeout ended the scan:** the scan read the field
  without the reopen-and-retry every other field read has. Now it uses the
  same recovery (reopen that gaussmeter once, read again).
- **Target box after Stop or an error:** a finished scan sets Target A to
  0.000; after Stop or an error it kept the pre-scan value although the
  scan wrote 0 A, so Up/Down stepped from it and Flip of 1.500 wrote
  -1.5 A (still behind the safety tick). Now it shows 0.000 when that 0 A
  write went out; the readback box is untouched (that 0 A is not read
  back).
- **Zero both now during Disconnect:** it queued behind the Disconnect
  (which writes 0 A, then closes the ports) and then failed with a "not
  connected" error dialog that replaced the Disconnect status. Now it is
  refused on the status line while a Disconnect is queued or running.
- **Zero both now pressed again and again:** each press queued another
  zero (on a silent BOP each ran its own ~15 s recovery, the page busy
  throughout). Now one zero queued or running is enough; further presses
  only say so on the status line and leave nothing set for the next
  command.
- **Estimated duration:** the start dialog and the logged estimate counted
  settle + acquire + 0.5 s per point, but each point also waits for a
  camera average begun after its current was set. Now a point counts
  max(settle + acquire, 2 x frames x exposure) + 0.5 s (0.7 s became
  1.3 s at 0.1 s / 0.1 s, 4 x 100 ms; the default 2 s / 1 s is unchanged).
- **Left as is (needs a decision):** the end of a scan still ticks
  Continuous although it was off before the scan, as Connect, Enable/zero
  and field zeroing do.
- **Tests:** `test_magnet_minor_review.py` (the PNG after Clear curve and a
  selector change, past 2000 points, a failed snapshot, no scan channel;
  the grab after GdiFlush/DwmFlush on Windows, the 50 ms fallback when
  DwmFlush is missing or fails, no wait elsewhere;
  a missed gaussmeter reply mid-scan; Target after Stop, after an error,
  not after a failed zero; Zero during Disconnect, a refused Disconnect,
  four presses during a Write and one during the zero, a zero not
  submitted; the estimate at 0.1 s / 0.1 s and at the default);
  `test_magnet_zero_and_frames.py` and `test_moke_scan_zero.py` updated for
  the new flags, the recovery read and the shared camera-block helper.
  Every dialog is mocked.

## CIDWM and MOKE-24: the row of a failed 6221 pulse, fields the scan and Connect do not use, Zero stays off (2026-10-13)

- **CIDWM, a failed 6221 pulse had no row:** in a 6221 pulse sign scan,
  `set_dc_pulse` sends OUTP ON and SOUR:WAVE:INIT before its readback
  checks. When a check failed (or INIT's error check, or the off after the
  pulse), the scan stopped without a CSV row for a pulse that may have
  fired. Now that pulse gets its row, as the Avtech's does: the set value,
  width, compliance and elapsed time, the readback when there is one, and
  the note "the scan stopped (the pulse may have fired): …" (or "the scan
  stopped after the pulse: …" when only the off failed). The 6221 is
  still switched off and the error still stops the scan.
- **CIDWM, refused by fields it does not use:** the 6221 scan's Start
  and the 6221 Connect parsed the whole pulse output box, so an |I| of
  150 mA or a Pulse count of 1.5 or empty (meant for Apply ±I) refused
  them. Now the scan reads only the box's width and compliance and Connect
  only its compliance, each still checked against the 6221's limits.
  Apply ±I still reads and checks the whole box.
- **MOKE-24, Zero switched the output on:** "Zero" needs no safety tick,
  but it called `apply_manual_voltage(0.0)`, which sends :OUTP ON when the
  output is off. So Zero after Connect (which clears the tick), after a
  scan or after the red "zero / output off" button switched the output on
  at 0 V without the tick. Now Zero on an output that is off sends 0 V
  and :OUTP OFF again, never :OUTP ON, and shows "Output: 0 V / OFF" only
  when that worked. On an output that is on it writes 0 V as before.
- **Left for the user to decide** (not changed): the MOKE-24 software
  limit equals the hardware OVP (200 V on a 2410, 160 V on a 2400), whose
  levels have a ±5 % tolerance, so a scan near the limit could be clipped
  by the OVP.
- **Tests:** `test_cidwm_6221_row_and_fields.py` (the real controller and
  scan worker with a stand-in 6221: a frequency mismatch after INIT, a
  timed-out INIT and a failed off after the pulse each leave their row and
  the 6221 off; the scan and Connect ignore the box's |I| and Pulse count
  but still check width and compliance; Apply ±I still refuses a Pulse
  count of 1.5); `test_moke24_zero_stays_off.py` (the real controller,
  submit and finish: Zero on an off output sends no :OUTP ON and shows
  OFF, a failed off is reported and not shown as OFF, Zero on an output
  that is on writes 0 V, Write still needs the tick). All fail on 0ad6a97
  except the three that guard unchanged behaviour (Apply ±I, Write needs
  the tick, Zero on an output that is on).

## Non-local: rack rows show Connecting…; Frequency Hz only when used; wording (2026-10-13)

- **Rack rows never showed "Connecting…" / "Disconnecting…":** Connect or
  Disconnect on a rack row wrote the word, and the controls update right
  after it wrote "Not connected" / "Connected" over it, so the Zurich's
  connect (tens of seconds) or the Keithley's drain after a Set above 20 V
  (up to 30 s) showed the old state. Now the row keeps its word through
  every refresh (this page's and other pages') until that Connect or
  Disconnect finishes, then shows the result.
- **An unused Frequency Hz refused the run:** a 6221-IV or Gate-VI
  frequency scan, and a Gate-VI run with a DC read current (where the entry
  is disabled), were refused ("must be valid numbers") when Frequency Hz was
  empty or invalid. Now it is read only where it is used: Set 6221, and an
  AC read current without the frequency scan. A frequency scan still takes
  the first frequency scanned as its frequency; a DC run logs
  `frequency_hz=nan`, as its CSVs already did.
- **"2th-order low-pass":** the Gate-VI start confirmation now says
  "low-pass filter order 2" for the Zurich. The 2026-10-09 entry below
  said "the 4th-order filter"; every Zurich page sets and checks order 2
  (`NONLOCAL_LOCKIN_FILTER_ORDER`), and that line now says so.
- **Gate-VI gate wording:** the code comment, the block message ("the most
  it can show") and the entry below said a Pulse gate above 4.9 GΩ can
  never pass, but a V/I measured at the 5 V limit (2e10 Ω at 0.25 nA, say)
  is kept and passes a 5000 MΩ gate. The behaviour is unchanged; the texts
  now say |R| is then at least the value shown, so such a gate may block.
  That entry's test count now says 10 of its 12 fail on the old code
  (checked on c3271af); the other 2 guard what is unchanged.
- **Tests:** `test_nonlocal_minor_review.py` (11; 8 fail on 0ad6a97, 3
  guard what is unchanged): Connect and Disconnect with the job held
  through the real controls update and a second update, then the finish
  (also a failed connect); 6221-IV and Gate-VI frequency scans with
  Frequency Hz '' or 'abc' give the settings of 17.3; a DC Gate-VI parse
  and a DC worker run with it empty (one curve per plot, `nan` logged); an
  AC run without the scan and Set 6221 still refuse it; the real start
  confirmation (dialogs mocked, declined); a measured 2e10 Ω over range
  passes a 5000 MΩ gate. `test_nonlocal_pulse_gate_6221.py`: the block
  message.

## PPMS / RT-E: RT-E SR830 addresses, the macro TEMP rate, decel code '5.0', unused KEPCO boxes (2026-10-13)

- **RT-E SR830 addresses could not be typed in:** RT-E builds the three
  SR830 addresses as plain entries, but the shared state update set every
  idle SR830 address to 'readonly' (meant for the PPMS page's address
  lists), so another GPIB address could never be entered. Now only a list
  (Combobox) is set 'readonly'; the RT-E entries are editable when idle and
  disabled while connected or busy, as before.
- **A macro TEMP line replaced the rate box with 1 or 10 K/min:** writing
  the line's target fired the trace meant for a typed target, which snaps
  the rate to 1 K/min (below 10 K) or 10 K/min. A later SCAN (which re-sends
  its fixed temperature) or Set T then used that rate, not the line's:
  `TEMP 250 3` then `SCAN` re-sent 250 K at 10 K/min. Now a TEMP with a rate
  also puts that rate in the box; without a rate the box keeps the trace's
  value, as before. The macro status line shows `auto` instead of `None`
  for a TEMP without a rate (the run log names the rate it picks).
  `PPMS_Macro_README.md` now gives that rate as it is since the warm-up fix
  (10 K/min only when the live temperature and the target are both above
  9 K, else 1 K/min) and says what a TEMP does to the rate box.
- **Decel code '5.0':** Move took it as code 5, Start scan refused it
  ("invalid literal for int()"). Start scan now parses it as Move does: a
  whole number 0–14, as '5' or '5.0'.
- **RT-E scan refused because of KEPCO boxes it does not use:** Start scan
  checked the KEPCO current A box and the voltage limit also in a Current
  scan (no KEPCO), and the current box in a Field scan (its currents come
  from the scan range). Now a Field scan checks only the voltage limit and
  a Current scan neither; Apply still checks both.
- **Tests:** `test_ppms_rte_review.py`: `RTESR830AddressesCanBeTyped`
  (RT-E entries idle, connected and busy; the PPMS page's lists and the
  Keithley entries unchanged), `MacroTempKeepsItsRate` (the real
  `_ppms_macro_next` and `_ppms_macro_operation` with the real target
  trace: with the box at 3, `TEMP 250 3` then `SCAN` sends 3, the SCAN sees
  3 and a later Set T sends 3; other explicit rates; a TEMP without a rate
  shows `auto`), `RTEStartIgnoresUnusedKepcoBoxes` (the real RT-E Start
  scan up to its submit: a Current scan with blank or out-of-range KEPCO
  boxes starts with no limit; a Field scan starts whatever the current box
  holds and refuses a bad limit; Apply still refuses either);
  `test_ppms_simple_modes.ModeTests.test_decel_code_is_parsed_as_move_parses_it`
  (the real Start scan with '5.0', '14.0', ' 0 ', and refusing '5.5', '15',
  '-1', 'nan', '1e3', each compared with Move's own check). The macro
  stand-in in `test_ppms_field_persistent.py` now has a rate box. Every
  dialog is mocked.

## FMR / STFMR: Connect during a sweep, tab names in conflicts, short loops, the Loop box, an unused 4886 (2026-10-13)

- **STFMR, Connect during a sweep:** the Connect / Disconnect buttons were
  only re-checked when a device connected or disconnected, so during a
  sweep the unused lock-in's Connect stayed live; connecting it switched
  the Meter selector, and the live X/Y/theta plots stayed blank for the
  rest of the sweep. Now Connect does nothing while a sweep runs (as
  Disconnect already did), a connect pressed just before Start no longer
  changes the meter when it finishes during the sweep, and every device
  row (Connect, Disconnect, address) is disabled from Start to the end of
  the sweep, then restored (a row still connecting keeps its state).
- **Connection conflicts named the wrong tab:** the KEPCO 4886, the LS475
  and the PNA of the tab called "FMR" were labelled "STFMR", so the
  message said to disconnect on the STFMR tab (both names read "STFMR /
  KEPCO ..."). They are now "FMR / ..."; the RT-E page's 4886 is "RT-E /
  ..." like its 475. The 4886 controller takes the page name
  (`owner_scope`, default "STFMR").
- **STFMR sweep with one or two currents per loop:** the first two points
  of every loop are discarded, so Start A = Stop A (a fixed-current
  frequency run) or a two-point loop switched the 4886 and the RF on and
  saved nothing. Start now refuses it before any dialog, file or output:
  "Each loop needs at least 3 current points ...".
- **STFMR "Loop" box:** an IntVar, so a cleared box gave Tcl's 'expected
  floating-point number but got ""' and 2.5 quietly ran 2 loops. It is now
  text, read at Start: a whole number of at least 1 ("2.0" is 2), anything
  else "Loop count must be a whole number of at least 1".
- **FMR readout of a 4886 sent nothing:** an FMR scan or frequency sweep
  stopped or failed while the PNA was set up (before the first 4886 write)
  showed "Current, 0 A, OUTP: OFF" and "4886 zeroed/OFF"; Disconnect showed
  "OUTP: OFF". Now the readout stays "--", the status says "4886 not used",
  and Disconnect shows "OUTP: --" (OFF once something was sent and switched
  off; NOT CONFIRMED OFF after a failed OFF, as before).
- **README_STFMR_DUAL.md** said PNGs were exported automatically and by
  Save CSV; neither happens (the exporter is not connected to the page, and
  the recording tests assert no PNG). The line now says so.
- **Tests:** `test_fmr_stfmr_minors.py` (Connect while sweeping and a
  connect finishing during a sweep keep the meter and live rows; the rows
  locked and restored; conflict messages for the FMR 4886, 475 and PNA,
  with fake VISA sessions; one, two and three currents per loop; Loop text
  "", 2.5, abc, 0, nan, "2.0"; both FMR scan ends before and after a 4886
  write; Disconnect with nothing sent, after an Apply, after a failed OFF).
  `test/test_stfmr_realtime_recording.py`: the two-point-loop run that
  completed with 0 saved points is now checked to be refused with no
  output or file.

## Camera: settings rows and saved images match the camera (2026-10-13)

- **Refused click left the radio moved:** during a MOKE magnet, MOKE-24 or
  CIDWM pulse scan a click on "Difference temporal average", "Binning" or
  "Mean filter" was refused, but the radio had already moved, so the page
  showed a value the camera never got, and the next click applied it. Now
  the three radios are set back to the camera's values before the warning
  opens (also after an invalid value).
- **Reconnect reset the camera, not the page:** after Disconnect/Connect
  or a camera error, the new camera started at 4 frames, 4×4, 2×2, 100 ms
  and no 100× marker, while the page still showed the operator's choices
  (and the first marker click did nothing). Now Connect gives the new
  camera the page's averaging, binning, mean filter, exposure (only when
  it differs; an invalid entry shows the camera's) and marker.
- **Saved display of an older moment:** with Camera Live hidden (it starts
  hidden) or paused, CIDWM "Save images" saved the last drawn display
  (minutes old, or nothing at all, refusing the raw and float32 too) under
  the same stamp as the current raw and float32. Now all files come from
  one frame; the display PNG is left out, with the reason in the status,
  when Camera Live has not drawn that frame. MOKE "Save current display"
  refuses a stale display with a warning.
- **Tests:** `test_camera_settings_rows.py` (each scan and each radio row;
  the next click after a refusal; disconnect and camera error, then
  connect, with the real worker; page defaults request nothing; the
  stand-in camera of the GUI test `test_camera_live_keys.py`, now the real
  worker with no camera thread, since Connect calls its setters and the
  old bare stand-in made that test's set-up fail);
  `test_cidwm_pulse_scan.SaveImagesTests` (an older display, never drawn,
  the real `_poll` with Camera Live hidden, Save current display). Both
  classes mock every dialog, so no window can open.

## MOKE magnet: Zero both now while busy, fresh scan frames, the Interval box (2026-10-13)

- **Zero both now was dropped while another command ran:** pressed during
  a Write / Up / Down / Flip / Zero current / Enable / Read (a Write whose
  readback retries can take 30 s or more), it only wrote "The previous
  magnet operation has not finished" to the status line, never sent 0 A,
  and left Continuous off. Now it queues behind the running command on the
  single magnet worker (no interleaved serial traffic); a running current
  write stops at its next attempt and writes 0 A, then the zero follows.
  The page stays busy until both are done (a count of pending commands).
  Other commands are still refused while one runs. If the zero cannot be
  submitted, Continuous is restored.
- **Current scan recorded the previous point at short Settle/Acquire:** the
  ROI value comes from the averaged difference image (4 frames plus its
  cross-fade, about 0.7 s at 100 ms), but samples were kept by wall-clock
  time only, so at the user's Settle 0.1 s / Acquire 0.1 s each point was
  roughly half the previous point's magnetisation (the loop looked wider)
  and some points were NaN. Now a point keeps only samples whose average
  began after its current was set and read back (the frame's
  `window_start_monotonic`, as the CIDWM scan does); if none has come when
  Acquire s ends it keeps acquiring, for at most two averaging blocks plus
  10 s, then records NaN. The default 2 s / 1 s scan is unchanged.
- **Start scan died silently on text in "Interval s":** '1,5', '0.5s' or an
  empty box raised TclError after Yes (no dialog, Continuous left off,
  nothing submitted). The interval is only log metadata; it is now read
  with the safe snapshot read ("unavailable" if not a number).
- **Tests:** `test_magnet_zero_and_frames.py` (a Zero pressed during a
  retrying Write's readback is queued, the Write gives up with no second
  write or recovery, busy until both end; other commands still refused;
  Continuous restored when the zero is not submitted; the scan worker at
  0.1 s / 0.1 s keeps only the sample begun after the set; NaN, not the old
  current, when no new frame comes; the camera passes the average start);
  `test_magnet_scan_start.py` (Start with '1,5', '0.5s', '' submits the
  scan); `test_moke_scan_zero.py` updated for the worker's new names.

## CIDWM and MOKE-24: 6221 failed-off interlock; Current limit locked while connected (2026-10-13)

- **CIDWM, what was wrong:** only the 6221's red button set the "6221
  output unknown" interlock when its off failed. A failed off after Apply
  ±I pulse, at the end of a 6221 pulse sign scan or while disconnecting left
  the Avtech free to pulse the sample, and Apply ±I and Disconnect even
  showed "Output: 0 mA / OFF". A pulse command that failed (a timeout, say)
  and whose off then failed showed only the off error.
- **CIDWM, the fix:** each of these finishes sets the interlock when the
  6221 off failed (output "check the 6221") and clears it when an off
  succeeded; after a failed Disconnect a reconnect (*RST and a checked
  OUTP OFF) clears it, and the Avtech warning says so. The pulse error and
  the failed off are both in the dialog (a readback mismatch whose own off
  fails still shows only the off error: that check is in the 6221
  controller, which MS-fourState.py copies).
- **MOKE-24, what was wrong:** Connect is the only reader of "Current limit
  (A)", but the field stayed editable while connected. A lowered limit
  looked applied (and could be ticked as safe) while scans, manual writes,
  the 21 mA refusal and the CSV kept the limit set at Connect.
- **MOKE-24, the fix:** the field is locked from Connect to Disconnect, as
  the Model list is, so it always shows the limit in use; the 21 mA
  refusal says "Disconnect, then Connect with a lower Current limit".
- **Tests:** `test_k6221_failed_off_interlock.py` (the real controller,
  worker and finishes with a 6221 whose OUTP OFF times out: Apply ±I (both
  errors kept), 0 A, the scan's final off, Disconnect; an off that works
  frees the Avtech);
  `test_moke24_current_limit.py` (locked while connecting, connected,
  scanning or writing; editable after Disconnect and the next Connect uses
  the new value; the refusal messages).

## Non-local: pulses end at Width s; the Gate-VI gate above 4.9 GΩ; 6221 OFF only once read back (2026-10-13)

- **Pulse width (2400-IV Apply pulse, every Gate-VI pulse):** the Keithley
  switch-off asked `:OUTP?` (the 50 ms query delay and the GPIB round trip)
  before it wrote 0 V, so every pulse stayed at its level about 55 ms past
  Width s (a 10 ms pulse lasted about 68 ms). It came with the review fix
  `13174fc`. Now 0 V is written first, then `:OUTP?` decides the drain; the
  drain at 0 V and the switch-off are unchanged.
- **Gate-VI pulse gate:** a sample above about 4.9 GΩ (5 V at 1 nA: an
  intact gate insulator or an open gap) raised "Resistance exceeds the
  measurable range" and stopped the scan, although the gate's rule is "a
  pulse needs |R| above it". Now that reading is a lower bound (the measured
  V/I, or 4.9 GΩ with no current) marked over range; the status line shows
  "|R|≥… (above the range at 1 nA)". |R| is then at least the value shown,
  so a Pulse gate above it may block (the message says the value is over
  range) although a measured V/I above the gate passes it.
  VI_resistance_gate.csv keeps its columns.
- **Set 6221 / 6221-IV:** with the sine on from Set 6221, a failed Keithley
  read-back (a GPIB timeout, or still on) at Set 6221 or 6221-IV Start
  stopped the task before the 6221 was switched off, and the page said
  "6221: OFF" / "6221: not switched on" with the sine still on. Now the
  Keithley check is inside the task's try, so the 6221 is switched off and
  read back in every case; "6221: OFF" only after that read-back ("6221:
  check the output" otherwise), and the Set status names the Keithley too.
- **Tests:** `test_nonlocal_pulse_gate_6221.py` (12; 10 fail on the old
  code, 2 guard what is unchanged): the 0 V right after the wait for
  -30 V / 0.01 s and 5 V / 0.1 s, with simulated query delays, then the drain and the switch-off; a failed
  0 V still drains and switches off; 5 GΩ, 20 GΩ, 1 TΩ and an open gap
  give a finite |R| ≥ 4.9 GΩ, 3 and 4.8 GΩ unchanged; the Gate-VI worker
  pulses past the 100 MΩ gate and blocks a 5000 MΩ gate with the reason;
  Set 6221 and 6221-IV Start switch a sine left on off with the Keithley
  read-back failing (or with a Stop), and never show OFF without the 6221's
  own read-back.

## PPMS / RT-E: descending current scans, exit keeps the field, warm-up from base (2026-10-13)

- **RT-E Current scan starting at its largest current** (e.g. 0.1 → 0.01 mA,
  or the single default 0.1 mA): the worker reserved the 6221 range as
  (I·1e-3)·√2, the configure check computes √2·I·1e-3; the two can differ
  by 1 ulp, so the scan failed at point 1 with "Invalid continuous current
  sweep range" after the CSV was made. Now the worker uses the validator's
  own expression, bit for bit; the strict check stays.
- **Closing the program during a Temperature, Angle or Current PPMS scan**
  ramped the field to 0 Oe in Driven mode. Now only Field and
  Field+Temperature scans zero the field on exit, as the worker, the exit
  dialog and `b458da8` already say; the others keep the magnet's setpoint
  and mode. The PPMS and instruments are still disconnected.
- **Warming up from at or below 9 K** (the limit is 1 K/min for the whole
  ramp when the start or the target is ≤9 K): Set T refused the 10 K/min
  the page fills in for T = 300; now Set T lowers it to 1 (the box shows 1),
  as it already does for targets below 10 K. A macro `TEMP 300` without a
  rate defaulted to 10, passed Validate and stopped at that line during the
  run; now an omitted rate is chosen at run time from the live temperature
  (10 only when both it and the target are above 9 K; the log names it),
  and an explicit `TEMP 300 10` after a `TEMP` at or below 9 K with no
  `SCAN` in between is refused at Validate. After a `SCAN` (which may be a
  Temperature scan that warmed the sample) Validate does not assume the old
  target; the run checks the live temperature, as before. The rate label
  now says "Start or target ≤9 K".
- **Tests:** `test_ppms_rte_review.py` (the real RT-E worker with the real
  6221 configure and live update: descending, single-point and ascending
  plans write one `SOUR:CURR:RANG` and every amplitude; exit for every scan
  mode, on the app and through the PPMS page; Set T from 5 K and above 9 K;
  macro TEMP with and without a rate at several live temperatures; an
  explicit `TEMP 250 10` after `TEMP 5` and a `SCAN` validates and is sent
  at a live 300 K, refused with nothing sent at a live 5 K).

## FMR / STFMR: long waits, fields above 6 A, idle SR830 reads, the 4886 readout (2026-10-13)

- **FMR field sweep, 'Wait s' above 15 s:** the 475 settle timeout was a
  fixed 15 s and was checked before the wait had ended, so every scan with
  a longer wait stopped at its first point with "did not settle", even with
  a stable field. The timeout now counts 15 s from the end of the requested
  wait (16 s with the default 1 s first-point wait).
- **Fixed-field frequency sweep, fields needing more than about 6 A:** the
  field feedback reused the 12 corrections of the MOKE Gauss zeroing; at
  0.5 A per step it could not get past 0.02 + 12 × 0.5 = 6.02 A, although
  the 4886 allows ±10 A. It now has its own limit,
  `STFMR_FIELD_FEEDBACK_MAX_ITERATIONS` = 12 + 10 A / 0.5 A = 32 (each step
  still at most 0.5 A, the current still clamped to ±10 A; MOKE zeroing
  unchanged). A field beyond ±10 A now ends with "needs more than the
  4886's ±10 A" instead of "current resolution". The start dialog's time
  estimate uses the new limit.
- **STFMR idle live read changed the SR830 range:** the 0.5 s live display
  (after Connect, no sweep running) autoranged from an assumed SENS 13 and
  ignored the 'SR830 autorange' tick, so Connect stepped the front-panel
  range one step per poll (and showed saturated values meanwhile). The live
  display now reads only (`read_sample(..., auto_range=False)`); sweeps
  still autorange as configured at Start.
- **FMR tab 4886 readout:** it started at "OUTP: OFF" and Connect showed a
  made-up "Current, 0 A, OFF" although nothing had been sent; a failed
  Zero / OFF (or a failed Apply whose clean-up also failed) left the old
  "OUTP: OFF", and "Show sent values" then said "OUTP: ON". Now, as on the
  STFMR page: "--" until something is sent; "OUTP: NOT CONFIRMED OFF" after
  any failed OFF, also at the end of an FMR or frequency scan, whose status
  line no longer says "4886 zeroed/OFF" when the zeroing failed.
- **Tests:** `test_fmr_stfmr_review.py` (fake clock and 475: a 20 s wait
  measures, an unsettled field times out at 35 s; a 300 G/A fake magnet on
  the real 4886 controller reaches ±9 A and stops at 10 A for 12 A; a fake
  SR830 at SENS 26 gets no write from the idle timer, in SR830 and
  MFLI + SR830 modes; a fake 4886 card stalling on OUTP OFF).

## MOKE magnet: Start scan says why it does not start (2026-10-13)

- **User:** "the start scan cannot be used".
- **Cause:** the scan code is unchanged since `38f351a`; two of Start's
  refusals only wrote the page's status line, so Start looked dead. One
  said "KEPCO not enabled" also when the real reason was the out-of-plane /
  in-plane selector not confirmed on the page.
- **Now:** every refusal is a dialog with its reason: the selector not
  confirmed; the KEPCO of the selected path not enabled ("Enable/zero
  (before writing)"); a magnet command still running; not connected.
- **The real fault** (the user's traceback): `_parse_magnet_scan` rounded
  each sequence item to 1 mA, but an item is (loop, point, points per loop,
  current), so `round()` raised TypeError and the scan never started. It
  came with the whole-program review fixes of 2026-10-09 (`ca5ee54`); no
  test ran the parsing. Now only the current is rounded.
- **Tests:** `test_magnet_scan_start.py` (the refusals; the parsing with
  the user's 5 → −5 → 5 A, 0.1 A, 1 loop: 201 points; two loops).

## Avtech_pulse_test.py: a standalone test of the Avtech generator (2026-10-13)

- **User:** an individual test script for the Avtech pulse generator.
- **`Avtech_pulse_test.py`** (repository root; needs only PyVISA): the same
  pulse sequence as the CIDWM page (reviewed twice), every command and reply
  printed. Modes: no argument = read only (identity, output, amplitude,
  width, delay, frequency, overload, error queue; queries only); `off`
  (hold, output off, read back); `pulse --amplitude V --width ns` (one
  pulse after typing "yes", or `--yes`); `--dry-run` (a simulated -P
  generator; nothing sent); `--resource` (default `GPIB0::8::INSTR`).
- **Exit status:** 0 done; 1 refused or failed with the output read back
  off; 2 usage error (nothing sent); 3 output state unknown.
- **Tests:** `test_avtech_pulse_test_script.py` (read only sends queries
  only; one pulse and off; the other sign refused; "no" fires nothing;
  another instrument gets only `*IDN?`; a stuck output is exit 3; limits
  checked before anything is opened).
- **Review** (one reviewer, each finding checked; all confirmed fixed):
  every Ctrl+C path ends with the output off or "CHECK THE GENERATOR" (the
  off sequence catches Ctrl+C as the program's controller does); before
  the identity is confirmed nothing but `*IDN?` is sent; pulse mode puts
  the trigger on hold and the output off (read back) before the question;
  a pulse interrupted after the trigger says it fired; connection and
  `*IDN?` errors are one line; the exit codes as documented; a negative
  amplitude example (`--amplitude=-10`). Tests: 10.

## CIDWM: the Avtech AVR-E3-B pulse generator (2026-10-13)

- **User:** add the Avtech AVR-E3-B-W3 (100 V, 500 ps, 1 ns - 5 us, 20 kHz)
  to CIDWM; a box of its own, and the pulse sign scan can use it or the
  6221; GPIB.
- **Commands from Avtech's manuals** (AVR-E2,E3-B sn14486 and ed9;
  programming manual for "-B" instruments, firmware 5.07): no Python driver
  exists (Avtech offers LabVIEW drivers only).
  - GPIB address 8 from the factory: `GPIB0::8::INSTR`.
  - One pulse is Avtech's "single non-repetitive event": `trigger:source
    hold`, `output off`, `pulse:width <s>` and `volt:ampl <V>` each read
    back (`pulse:width?`, `volt:ampl?`), `syst:err?`, `output on` read back,
    `trigger:source immediate` (the remote Single Pulse button),
    `trigger:source hold`, `syst:err?`, then `trigger:source hold`,
    `output off`, `output?` = 0, and `output:protection:tripped?`.
  - Connecting (after `*IDN?` names AVTECH), disconnecting, closing the
    program and the red "Avtech output off" button leave the trigger on hold
    and the output off.
- **Safety:** amplitude above 0 and at most 100 V, width 1 ns - 5 us (-W3);
  a tick "50 Ω load at the output, wiring and amplitude confirmed safe";
  the polarity is the unit's (-P, -N or -PN, read from `*IDN?`): a sign the
  model cannot give is refused, and with any model a pulse fires only if the
  amplitude read back has the sign and size asked for (within 2 % or 0.5 V)
  and the width within 5 %. An overload (output protection tripped) is
  reported, and stops a scan.
- **Box:** Resource, Connect, Disconnect, lamp; Amplitude |V| (V) (10),
  Width (ns) (10); Apply +V pulse, Apply −V pulse, Avtech output off; the
  read-back of the last pulse. Defaults: 10 V, the smallest amplitude Avtech
  advises without an attenuator, and 10 ns.
- **Pulse sign scan:** "Pulse source: 6221 / Avtech" and its own |V| V (the
  width from the Avtech box). After each pulse, as before: the wait, a
  camera frame wholly after the pulse, its display PNG, raw and processed
  TIFFs, a CSV row. The Avtech CSV: set_amplitude_V, amplitude_V,
  pulse_width_ns (read back), overload_tripped; files `..._+20V_...`. The
  parameter log names the source and the Avtech's identity. Stop ends after
  the pulse in progress, the Avtech off.
- **Tests:** `test_avtech_pulser.py` (a simulated generator: the sequence,
  the polarity refusals, an output left on, overload, limits, connecting);
  `test_cidwm_pulse_scan.py` (the scan on the Avtech, an overload);
  `test_panel_defaults.py`, `test_stfmr_field_output.py` (the box, CI).
- **Review** (three reviewers, each finding checked by a second; all
  confirmed findings fixed):
  - the off path sends "output off" even when the hold write fails (each
    command on its own, a device clear and one retry), then reads it back;
  - the 6221 and the Avtech never pulse together: each waits for the other
    to be idle (box buttons and the scan); a 6221 Disconnect no longer ends
    an Avtech scan's state; the 6221's red button switches the 6221 off
    during an Avtech scan too;
  - a pulse that fired and then failed (a timeout, an error after it, the
    output not reading back off) is "pulse fired, then: ...", and a scan
    writes its CSV row before stopping; error 1001 after the pulse is an
    overload, not a failure;
  - `pulse:delay 0` read back before the output goes on (a front-panel delay
    of up to 1 s would come after the output went off);
  - `*CLS` at connect and before each pulse; no `SYST:LOC` on disconnect
    (not an Avtech command: it queued an error that refused the next pulse);
  - a NaN read-back is refused; the polarity is read from the model field
    of `*IDN?` only; the scan takes only the box's width; the safety tick
    refreshes Start; the red button is off during a connect/disconnect;
  - overload means a short or too-low load impedance; a missing load is not
    detected (said in the tick and here).
- **Second review** (of the fixes; all confirmed findings fixed):
  - each pulse starts with a device clear and `*CLS`, THEN hold and off,
    whose errors and the output (read back off) are checked before anything
    else: a `*CLS` after them had hidden a refused hold, and with internal
    triggering left on the output could have pulsed freely;
  - the off path clears the device first, sends hold and off each with a
    retry, and "output off" once more if the output does not read back off;
  - connecting reads the output back off (else it closes the session and
    says "check the generator"); a failure keeps every message;
  - an overload already read (1001) is kept when a later query fails; an
    unreadable overload state is "unknown" in the CSV; the box's error
    message says OVERLOAD;
  - "the trigger may have been sent" when the trigger write itself failed;
  - the 6221's red button only signals a running 6221 job (it switches its
    own output off), so it no longer frees the 6221 for the Avtech while a
    newer train runs; a 6221 off that failed shows "check the 6221" and the
    Avtech waits until an off succeeds.
- **This unit's polarity:** Avtech's ed9 manual says -PN is not offered on
  -W3 units, so this generator very likely gives one polarity only. A pulse
  of the other sign is refused before anything is fired; for ± pulses use a
  reversing switch, or the 6221.
- **Not tried on the instrument yet.**

## Drift tracking: two steps, windowed, band-passed, to a few hundredths of a pixel (2026-10-13)

- **User:** "the drift tracking seems not working very well": in the
  difference, every particle was a bright/dark pair and the device edges
  light/dark lines (a residual shift), with "match response=0.298".
- **The old estimate:** `cv2.phaseCorrelate` on the 4x-downsampled frames,
  no window, the result times 4. The frame border and the vignetting (fixed
  to the optics, not moving with the sample) pulled it towards zero, and its
  sub-pixel error was multiplied by 4. On simulated 2048 x 2048 frames
  (`Other-files/ci/drift_benchmark.py`, run on CI) it was off by up to
  0.51 px.
- **Now** (`estimate_drift_shift`):
  1. Coarse: the whole frame, 4x downsampled, Hann-windowed, phase
     correlation weighted by a band-pass (noise above, illumination,
     vignetting and slowly varying domain contrast below), within ±80 px.
  2. Fine: a 768 x 768 full-resolution centre window at the coarse shift,
     the same correlation within ±3 px, the peak fitted (Gaussian) to a
     fraction of a pixel.
  - Phase correlation, not plain correlation: plain correlation slid by
    pixels along one strong straight edge (every frequency now counts by its
    phase only, so the many particles count).
  - Simulated frames: worst error 0.026 px (old 0.51 px); 26 ms per frame
    on the Windows CI machine (old 19 ms).
  - The background's spectra are prepared once per captured background
    (`DriftBackground`), off the per-frame path.
- **Match** (status line and Camera Live overlay): 1 for identical frames,
  about 0.6 for a correct match on noisy frames, near 0 for unrelated ones.
  A frame whose coarse match is under `DRIFT_MIN_MATCH` (0.1: dark, or not
  the background's scene), or whose drift is beyond ±80 px, is not shifted,
  as before.
- **Review** (three reviewers, each finding checked by a second): no defect
  confirmed. Taken from it: a true Hann window on both paths
  (`cv2.createHanningWindow` is a sine window), the coarse peak searched
  over the whole plane (as `cv2.phaseCorrelate` did), the match floor.
  Not taken: a fine-versus-coarse match gate (it helps only a centre with a
  single straight edge and nothing else, unlike the CIDWM view), temporal
  smoothing of the shift (errors at 100 ms are about 0.03 px).
- **Shift of the frame:** bilinear, as before (cubic left more noise in the
  difference on the simulated frames).
- **Tests:** `test_drift_tracking.py` (no window): sub-pixel shifts within
  0.05 px with fixed vignetting, a 39 px shift, OpenCV and NumPy agree, the
  match tells a good frame from an unrelated one, a 100 px drift or a dark
  frame is not shifted.

## Camera Live: [C] and the other keys after a click; what [C] did, in the picture (2026-10-13)

- **User:** [C] (clear ROI) did nothing on the lab PC.
- **Checked on CI** (Linux and Windows, a stand-in camera): [C] clears the
  ROI, also after a Tk folder dialog and a message box. So on the lab PC the
  key did not reach Camera Live: the keyboard was still in the main window,
  or the Chinese input method took the letter.
- **Fix:** a click in the picture (Shift-drag for an ROI too) gives Camera
  Live the keyboard and English input mode, so [A] [B] [N] [C] [R] [M] [Q]
  reach it.
- **Shown in the picture for 2.5 s:** "[C] ROI cleared", "[C] no ROI to
  clear", or "[C] ROI not cleared: a scan is running" (also in the log).
- **Test:** `test_camera_live_keys.py` (needs a display: CI).

## English Tk dialogs in MS-fourState and the microwave program (2026-10-13)

- **User:** the CIDWM folder chooser (Tk's own dialog) in the other programs too.
- **Main program:** already on every page: all 24 folder, open and save
  dialogs go through the Tk commands `use_english_tk_dialogs` replaces.
- **MS-fourState.py and Test_Microwave_NH_dev.py:** the same code (Tk's own
  folder, open and save dialogs; English Yes/No boxes), copied from the main
  program by `Other-files/tools/ms_fourstate_copy_core.py` (MS-fourState in
  its copied block; the microwave program in its own "English dialogs"
  block), installed when each program's window is built.
- **One standard box per process:** tkinter's own `_show` is kept on
  `tkinter.messagebox`, so copies in one process never fall back on each other.
- **Tests:** both copies match the main program; both programs install the
  dialogs with their window; the CI checks fail if a program's window on
  Windows uses the native dialogs.

## Review of the start-up fix (2026-10-12)

Four read-only reviewers (the guard, every other pair, start-up risks since
`38f351a`), each finding checked by a second agent.

- **Non-local Gate-VI and 6221-IV (confirmed):** the settings box sits in a
  column without weight, beside the scan box, and holds its own pair: the
  shape that hung CIDWM. After a jump in width (maximize from a narrow
  window) its pair stayed stacked for the session. Now, while side by side,
  the settings column keeps the room its own pair needs
  (`_keep_pair_left_room`, RT-E's `keep_settings_room`).
- **Message box over a Tcl window (confirmed):** if a Combobox's open list or
  Tk's file dialog held the grab, the box raised KeyError and stayed on top
  for good. The previous grab is now kept by its Tcl name and given back; a
  box that fails while it is built is removed and the standard box shown.
- **Kept as is:** the guard's one-second window (refuted: the program's
  pairs take their width from the window, and a slower reset would hold
  pairs whose labels update twice a second). A hold lasts until the pair
  next tries to flip at another window width.
- **Tests:** the loop test also visits the Non-local sub-pages and the
  maximized 2560 px width; `test_panel_defaults.py`: a box while a Tcl
  window holds the grab.

## MOKE motor: a 6400 µm quick position; version 1.0.0 (2026-10-12)

- **6400 µm button:** beside 7250 µm in "Position and step (µm)". Like
  7250 µm, it fills in the absolute position and runs Go to absolute
  position: the same ±100 mm range check, safety tick and Yes/No
  confirmation, and it stays disabled until the stage is homed.
  6400 µm is 655360 counts (×102.4).
- **Version:** `APP_VERSION` is `1.0.0` (was `V1`); it is written into each
  scan's settings as `software.version`.
- **Test:** `test_motor_presets.py`: 3 tests, no window.

## Start-up hang fixed (2026-10-12)

- **Symptom:** from `66f807b` on, the program never opened its window on
  the lab PCs; the main thread sat in Tk's main loop. CI hung the same way
  (`root.update()` in `test_panel_defaults.py` never returned).
- **Cause:** on CIDWM, the "MOKE difference display" box sits beside the
  Images box, and the spare width went to the Images side. The display's
  width then followed its own mode row (Off / Plain difference / Drift
  tracking beside Recapture BG / 100× marker). That row flipped between
  side by side and stacked at every `<Configure>`, thousands of times a
  second, so the window never finished drawing. A CI probe that counted
  Tk callbacks found it (one row, 628 px needed, given 628 and 379 in turn).
- **Fix:** the difference display takes the spare width, as on MOKE magnet
  (`moke.columnconfigure(0, weight=1)`).
- **Guard for every page:** a side-by-side pair that flips more than
  `PAIR_FLIP_LIMIT` (8) times within a second at one window width stays
  stacked (never clipped) until the window is resized.
- **Also:** `main()` is as it was before (no start-up watchdog: dumping a
  running thread from `faulthandler` crashed Python 3.11 on the lab PC).
  The English message box falls back to the standard one for a parent that
  is not a Tk window or if the box cannot be built. CI prints each test's
  name and stops a test that hangs for 5 min with its stack
  (`Other-files/ci/run_suite.py`).
- **Test:** `test_pair_layout_loop.py`: no pair needs the guard on any page
  at 2000, 1101, 826 and 1574 px; with the old CIDWM weights the window
  still finishes drawing, the looping row held stacked.

## CIDWM: the scan's own pulse size, the display saved too; "Camera settings" (2026-10-12)

- **Pulse size:** the pulse sign scan has its own "|I| mA:" box (default
  2 mA), checked against the 6221's limits. Width and compliance still come
  from the pulse output.
- **Display saved too:** after each pulse the scan also saves the display,
  `…_display8.png` (8-bit, no overlays), and the CSV gains `display_png`.
  - The display is that of a frame wholly after the pulse. The raw and
    processed TIFFs are of that same frame, so all three match.
  - If Camera Live does not draw such a frame within 3 s (window hidden),
    the TIFFs are saved and the note says the display was not.
  - The camera window keeps what it shows as one value (`latest_display`:
    image, geometry, frame).
- **Camera settings:** on CIDWM, the CLim, temporal average, binning, mean
  filter, exposure and recording rows are in a box named "Camera settings".
  The MOKE ROI editing box was already removed (it is in the user's older
  screenshot).
- **Tests:** `test_cidwm_pulse_scan.py`: 18 tests.

## Camera display defaults from the user's figure (2026-10-12)

- **New defaults:** difference temporal average 4 frames, binning 4×4, mean
  filter 2×2, exposure 100 ms. They were 1 frame, 1×1, off and 20 ms.
- **Already as in the figure:** CLim −2 ~ 2, FPS 10, Refresh 0.2 s,
  Points 100, edit steps A 1, W 1, H 1, X 5, Y 5.
- **One source:** the page (MOKE magnet and CIDWM) and the camera worker
  both start from `EXPOSURE_S`, `DIFFERENCE_TEMPORAL_AVERAGE_FRAMES`,
  `DIFFERENCE_SPATIAL_BINNING` and `DIFFERENCE_MEAN_FILTER_SIZE`, so the
  first frames are processed as the page shows.
- **Test:** `test_panel_defaults.py` checks the values.

## CIDWM: no ROI editing or live ROI plot; one Save images button (2026-10-12)

- **Removed:** the "MOKE ROI editing" box and the live "ROI mean intensity
  vs time" plot (user). MOKE magnet keeps its ROI editing.
- **Save images:** an "Images" box beside the difference display holds one
  button.
  - **What it saves:** in the folder you choose, from the same moment:
    - the current Camera Live display, 8-bit PNG without overlays
      (`CIDWM_<time>_display8.png`);
    - the raw 16-bit TIFF (`…_raw16.tiff`);
    - the difference raw image, float32 TIFF
      (`…_TrackingDiff_Avg4_Bin2x2_Mean1x1_float32.tiff`).
  - **Without difference on:** it saves the other two and says so.
  - **Feedback:** it reports in the status line, with no pop-up unless the
    save fails, and is held while saving.
- **Scan:** the pulse sign scan still records the ROI mean of each
  processed image when an ROI is selected (on MOKE magnet or in the camera
  window).
- **Tests:** `test_cidwm_pulse_scan.py`: 14 tests (3 for Save images).

## CIDWM pulse sign scan; every dialog and label in English (2026-10-12)

### Pulse sign scan (CIDWM)

The user asked for a scan that sets the pulse count and each pulse's sign,
and saves the raw TIFF and the processed TIFF after each pulse.

- **Row:** "Pulse sign scan (raw + processed TIFF after each pulse)" sits
  under the pulse output. Its fields are Pulses (10), Signs (+-) and Wait
  after pulse s (1).
  - Signs holds + and − for each pulse and repeats to fill Pulses: +-
    alternates, ++-- goes in pairs.
  - Each pulse uses the pulse output's |I|, width and compliance.
- **Each pulse:**
  1. One pulse at ±|I|; then the 6221 is off.
  2. Wait after pulse s.
  3. Wait for the first camera frame whose processed image holds only
     frames that began exposing after the pulse ended. The camera's block
     average with its cross-fade can hold up to 2N−1 frames. Each frame now
     carries `window_start_monotonic`, the exposure start of the oldest frame
     in its average (arrival less exposure, less 50 ms). The wait lasts at
     most 10 s plus two averaging blocks; otherwise the scan stops with an
     error.
  4. Save that frame's raw 16-bit TIFF and its processed (difference)
     float32 TIFF:
     `CIDWM_pulse00001_pos_+2mA_raw16.tiff` and
     `…_TrackingDiff_Avg4_Bin2x2_Mean1x1_float32.tiff`.
  5. Write a CSV row: sign, current, time, compliance at readback, the ROI
     mean of the processed image, the two file names, and a note.
     - Every pulse applied has a row. A Stop or an error after a pulse
       leaves a note ("stopped before the images were saved" or "images not
       saved: …").
     - `in_compliance_at_readback` is read just after INIT, by which time a
       µs pulse may have ended.
- **Before it starts:** it needs the camera running with plain or
  drift-tracking difference on. The confirmation shows the signs and the
  time. It writes a run folder `CIDWM_pulse_sign_scan_<time>` with a
  parameter log.
- **Stop:** Stop, or the red button, ends it after the current pulse. The
  6221 is off after each pulse and at the end, whatever happens. If switching
  it off fails, the page says to check the 6221.
- **Tests:**
  - `test_cidwm_pulse_scan.py`: 11 tests, no Tk, including the averaging's
    window start.
  - `test_panel_defaults.py`: the row.

### Every window in English

- **Dialogs:** on Windows, Tk's message boxes and file dialogs were the
  Windows ones, with buttons in the language of Windows (是/否 on the lab
  PC). `use_english_tk_dialogs` changes that.
  - **Message boxes:** every `messagebox.*` call goes through
    `english_message_box` (it replaces `tkinter.messagebox._show`). Each box
    is its own window with its own answer, and its buttons stay at the
    bottom, so a long message cannot push them off the screen.
    - Why not Tk's own box (review, 2026-10-12): it shares one window and
      one answer, so a box popping up over another closed it and gave it
      the second's answer.
  - **File dialogs:** tk_getOpenFile, tk_getSaveFile and tk_chooseDirectory
    (and Tcl's tk_messageBox, used by their "overwrite?") are Tk's own
    dialogs, with msgcat in English. They look like Tk's, not Explorer's.
  - **CI:** it opens a real Yes/No box and checks it reads Yes / No. It also
    opens a box over an open one and checks that both answers are right.
- **Punctuation:** about 80 texts used full-width Chinese punctuation, such
  as "ROI mean：", "OUTP：OFF", "；", "，", "（）" and "～". They now use ASCII
  punctuation. MS-fourState's copied code was refreshed with it.
  - The output-off error lists are joined with " | ", because
    `short_status_error` ends a status line at "; " and the status bar
    would otherwise show only the first error.

## CIDWM: negative pulses (2026-10-12)

- **The buttons:** "Apply +I pulse" and "Apply −I pulse" replace "Apply pulse
  output". Both use the size typed in "Pulse current |I| (mA)", with the
  button's sign, so the direction changes without retyping. The box title is
  now "DC unipolar pulse output (0 A ↔ ±I, 50% duty)".
- **The 6221:** a −I train is the same square wave between 0 A and −|I|
  (amplitude |I|/2, offset −|I|/2). The count, width and total time are as
  for +I. The 6221 always accepted a negative current; only the page did not
  offer it.
- **Tests:**
  - `test_cidwm_pulse_polarity.py`: 3 tests, no Tk.
  - `test_panel_defaults.py`: the buttons.

## CIDWM: MOKE-6221 renamed and moved, MOKE imaging from MOKE magnet, no pulse current scan (2026-10-12)

The user asked to rename MOKE-6221 as CIDWM, put it between MOKE magnet and
MOKE-24, delete its pulse current scan, ROI editing and both plots, and add
MOKE magnet's difference display and ROI editing.

- **Tabs:** MOKE light, MOKE motor, MOKE magnet, **CIDWM**, MOKE-24,
  Non-local, PPMS, RT-E, FMR, STFMR.
- **CIDWM, top to bottom:**
  - The Keithley 6221 / VISA box beside the DC unipolar pulse output (as
    before).
  - "MOKE difference display" beside "MOKE ROI editing".
  - The display rows: Difference CLim, temporal average, binning, mean
    filter, exposure, Camera Live recording.
  - "ROI mean intensity vs time (live)".
- **Shared with MOKE magnet:** both pages' widgets use the same variables
  and state lists, because there is one camera and one ROI. Changing the
  CLim, averaging or exposure on either page changes both.
- **Not on CIDWM:**
  - MOKE ROI editing's "Clear curve" and "Field" belong to the magnet scan's
    plot, so they stay on MOKE magnet only.
  - The ROI nudges report to the CIDWM status line.
  - The saved images are named `CIDWM_…`.
- **Removed:**
  - The "Pulse current scan and ROI acquisition" box: the current
    round-trip scan, the fixed-current pulse acquisition, Save CSV and the
    post-pulse raw images.
  - The 6221 page's own ROI editing box and the pulse-current-vs-ROI plot.
  - The scan's methods, state, constants and `build_6221_current_sequence`.
  - Three scan tests in `test/test_instrument_safety_logic.py`.
  - `k6221_scan_running` stays, always False, so the other pages' "a scan
    is running" checks are unchanged.
- **Refactor:** `_build_moke_difference_box`, `_build_moke_roi_edit_box` and
  `_build_moke_display_controls` build the panels for both pages. Recapture
  BG and the 100× marker button are now lists
  (`difference_capture_buttons`, `objective_100x_marker_buttons`).
- **Tests:**
  - `test_panel_defaults.py`: the tab order and the CIDWM panels.
  - `test_stfmr_field_output.py`: the CIDWM layout, and the red stop button
    is never clipped.
  - `ci/gui_checks.py` checks CIDWM as a compact page.

## MOKE-6221: no AC/DC continuous output; pulse defaults from the user's figure (2026-10-12)

- **Removed:** the "AC/DC continuous output and live readback" box and its
  handlers (Apply continuous output, its Stop / zero, the mode, current and
  frequency boxes).
  - `Keithley6221Controller.set_continuous_output` stays, because
    `MS-fourState.py` copies the controller.
- **Moved:** the pulse output box now sits beside the connection box, where
  the AC/DC box was.
  - The pulse box has the fields on its first line. Apply pulse output and
    the red stop button are on the second line, with the live readback (OUTP
    state and the compliance light, read every 0.3 s while idle) and the
    output set.
  - That keeps the two boxes side by side at the lab's 2000 px window with
    the Windows fonts.
- **Plots:** both MOKE-6221 plots now also redraw when the page is shown
  (`<Map>`). The live ROI plot had stayed blank until data arrived when the
  window's last resize came while the page was hidden.
- **Pulse output defaults:** 2 mA, 50 µs high-level width, 1 pulse,
  compliance 100 V (were 1 mA, 10 µs, 50 V).
  - New constants: `K6221_OUTPUT_PULSE_WIDTH_US`, `K6221_OUTPUT_COMPLIANCE_V`.
  - The scan and train widths keep 10 µs.
  - The controller's own start values are unchanged.
- **Tests:** `test_panel_defaults.py` checks the defaults and that the box is
  gone; the `test_stfmr_field_output.py` layout check now pairs the
  connection with the pulse box.

## Non-local: Gate-VI scan defaults from the user's figure (2026-10-11)

- **New defaults:**
  - Start V 100, Stop V −100, Step V 10 (were 20, −20, 4).
  - 6221 pre-run 5 s (was 10).
  - Measure/pt 30 s (was 50).
- **Unchanged:** pulse width 0.1 s, post-pulse wait 0.5 s, pulse gate
  100 MΩ, Sample Hz 1, Loops 2, frequency scan off at 7–97 Hz.
- **High voltage:** ±100 V puts the 2400 on its 200 V range, with its
  120 V OVP. The start confirmation shows the HIGH VOLTAGE note.
- **Scan size:** one loop is 41 pulses; the default 2 loops are 82.
- **Tests:** `test_nonlocal_gate_vi_harmonics.py` and
  `test_panel_defaults.py` check the values.

## Non-local: Loops on the Gate-VI scan (2026-10-11)

The user asked for a loop count on the Gate-VI scan, default 2.

- **Where:** "Loops:" sits at the start of the frequency-scan row, so the
  box is no taller.
- **What a loop is:** each loop is a whole pulse scan, start → stop → start.
  The next loop starts again at Start V, so the Start V pulse is applied at
  the end of one loop and again at the start of the next.
- **Frequency scan:** the loops run at each frequency in turn
  (f1: loop 1, loop 2; f2: loop 1, loop 2 …), as on 6221-IV.
- **Limits:** a whole number from 1 to 10000, as RT-E's Loops. All loops and
  frequencies together are at most 10000 points.
- **Time:** the estimate, the point count and the progress bar count every
  loop.
- **CSV:** `VI_raw.csv`, `VI_summary.csv` and `VI_resistance_gate.csv` gain
  a last column, `loop`. It is empty for the initial |R| check.
- **Plots:** the |R| and voltage-vs-pulse plots draw a line per loop, in the
  same colour, with no line from one loop back to the next. With a frequency
  scan, a curve per frequency and loop.
- **Tests:**
  - `test_nonlocal_gate_vi_loops.py`: 9 tests, no Tk.
  - `test_panel_defaults.py`: the default and the box.

## Non-local: 2400-IV defaults −10 → 10 V (2026-10-11)

As in the user's figure, 2400-IV starts at Start −10 V, End 10 V, Step 1 V,
Settle 0.5 s: 21 points. The defaults were −50 V, 50 V and 5 V. The sweep now
stays on the 2400's 20 V range. `test_nonlocal_keithley.py` and
`test_panel_defaults.py` check the values.

## Non-local: frequency scan on 6221-IV and Gate-VI (2026-10-10)

The user asked for a frequency scan on 6221-IV and Gate-VI. It follows the
STFMR multi-frequency sweep: the whole scan runs at one frequency, then at
the next. The frequencies are chosen as on the microwave test program's
modulation axis.

- **The row:** each scan box has "Frequency scan", "f start Hz" and
  "f stop Hz".
  - Off by default: the scan runs at Frequency Hz, as before.
  - Ticked: the scan uses the whole-number primes from f start to f stop
    (default 7–97 Hz, 22 primes). The 2nd and 3rd harmonics of a prime never
    fall exactly on a 50 or 60 Hz mains harmonic. Some come within 1 Hz,
    e.g. 3 × 17 = 51 Hz, 59 and 61 Hz.
  - A note beside the boxes gives the count, or what is wrong.
  - Frequency Hz is not used, and not checked, while the tick is set. The
    parameter log's `frequency_hz` is then the first frequency scanned.
  - The row is held while a measurement runs.
- **Between frequencies:** the 6221 is switched off and read back off, then
  set up again at the next frequency. The lock-ins are set up again too.
- **6221-IV:** the whole current scan (all loops and harmonic passes) runs
  at each frequency. The first point waits at least 5 periods of the new
  frequency, besides the 10 time constants. The lock is checked again at
  each frequency.
- **Gate-VI:** the whole pulse scan (start → stop → start) runs at each
  frequency.
  - The 6221 pre-run is at least 5 periods.
  - The point index runs on across frequencies.
  - At each new frequency every SR830 order starts again from the
    configure range, not the range it ended on at the last frequency.
  - The frequency scan needs an AC read current: the row is greyed out with
    DC, as Frequency Hz is.
- **Plots:** one curve per frequency, in each meter's colour, light at the
  lowest frequency to dark at the highest, as on STFMR. The legend names
  every frequency up to six, otherwise the lowest and highest. Gate-VI's
  |R| and voltage-vs-pulse plots do the same. With one frequency the plots
  are as before.
- **CSV:** unchanged columns.
  - 6221-IV and the Gate-VI raw and summary CSVs give each row's frequency
    (`frequency_Hz`, `6221_frequency_Hz`).
  - `VI_resistance_gate.csv` has no frequency column. Its rows match the
    summary by `point_index`, and `VI_summary.csv` also carries each point's
    |R| (`post_resistance_Ohm`).
- **Limits:**
  - The demodulation limits (SR830 102 kHz, Zurich 500 kHz) are checked at
    the highest frequency.
  - 6221-IV: at most 100000 points, including passes and frequencies.
  - Gate-VI: at most 10000 points, all frequencies together.
  - The confirmation shows the count and the estimated time.
- **Review (2026-10-10):** an independent review found no hardware-safety
  defect. The outputs are off and read back off at each frequency change,
  on Stop and on errors, and the tick-off path is unchanged. Its low
  findings are fixed:
  - the unused Frequency Hz is no longer checked;
  - each SR830 order starts afresh at a new frequency;
  - the 6221-IV row is held during a run;
  - the 6221 line says OFF while it is set up again;
  - the 6221-IV time estimate is corrected.
- **Tests:**
  - `test_nonlocal_frequency_scan.py`: 21 tests, no Tk.
  - `test_panel_defaults.py`: a check of the rows.
  - The CI screenshots show a three-frequency 6221-IV example.

## Non-local: every AC frequency defaults to 17.3 Hz (2026-10-09)

The user asked for 17.3 Hz as every default frequency on the Non-local page.

- **Gate-VI scan:** the 6221 frequency now starts at 17.3 Hz (it was 13.7 Hz).
- **6221-IV** was already at 17.3 Hz (RT-E's value). It and Gate-VI now take
  `NONLOCAL_FREQUENCY_HZ`, so the page has a single default.
- **MOKE 6221 page, continuous AC output:** it used to share that constant.
  It keeps 13.7 Hz through its own `K6221_CONTINUOUS_FREQUENCY_HZ`.
- **Test:** `test_panel_defaults.py` checks the three non-local frequencies
  (17.3) and the MOKE 6221 frequency (13.7).

## PPMS: first-point settling where it belongs; "I off" keeps the field (2026-10-09)

The user asked to fix two items left open by the whole-program review.

- **First-point settling** (repeated windows until two agree) now runs only
  where the lock-ins have just seen a change of state:
  - the first point of a field sweep, after the field jump;
  - the first point after the 6221 was (re)configured (`ppms_source_fresh`).
    That is every temperature point, whose source is reconfigured, and the
    first angle and the first current point.

  The other angle and current points, where the source keeps running, take
  one acquisition window as any other point. Before, every one of them
  could take up to 10 windows.
- **Zurich noise floor:** the settle test now gives the Zurich channels a
  floor of 0.2 % of the Signal Input range, as the SR830s have 0.2 % of
  their sensitivity. A weak 2nd or 3rd harmonic no longer holds a point for
  all 10 windows.
- **"I off", Stop or exit during a non-field scan** (temperature, angle,
  current) no longer ramps the field to zero. Only Field and
  Field+Temperature scans zero it, as the exit dialog says. A persistent
  field set before a temperature scan stays.
- Tests: `test_ppms_nested.py` (stop keeps or zeroes the field by mode; only
  the first angle settles; the Zurich floor).

## Whole-program review: fixes (2026-10-09)

The user asked to check the whole script and optimise its logic. Five
read-only reviews covered Non-local, PPMS/RT-E, STFMR/FMR, the MOKE pages
with the camera, and the shared code. Each finding was checked against the
code before it was changed. Tests: `Other-files/test_review_fixes.py`, plus
updates in the page tests.

**Instruments left on, or switched back on**
- MOKE magnet Disconnect wrote nothing: the KEPCOs stayed at their current,
  and exit then skipped the closed channels. It now writes 0 A first.
- FMR: the red Zero / OFF did not stop a running scan, whose next frequency
  switched the KEPCO back on. It stops the scan first.
- FMR: "Wait s" was re-read mid-scan; a half-typed value stalled the scan
  with the magnet on. The scan uses the values validated at its start.
- RT-E KEPCO Zero / OFF acted only on an output this program had switched
  on, yet said "sent". It now uses `force_output_off`, as the STFMR page does.
- Non-local: the 0 V drain ran with the Keithley output already off. It
  timed out, its flag stuck, and the Keithley stayed unusable until a
  restart. It now drains only with the output read back on.
- MOKE-24 Keithley: a manual voltage is switched off before a scan changes
  range and OVP.
- LED Disconnect switches the LEDs off first. A camera failure stops the
  running MOKE scans. Exit tells every page's scan to stop at once, and one
  panel's close failing no longer skips the next.

**Data**
- MOKE: each redraw (pan, zoom, CLim) fed the same camera frame into the
  scan's ROI samples again. Each frame now counts once.
- MOKE: exposure and averaging are refused during a scan. An ROI change no
  longer deletes finished 2410/6221 results.
- STFMR SR830: the overload latched by its own range change looked like a
  new overload, so the range walked to the top and aborted the scan. The
  latched status is now read and dropped, as on the Non-local page.
- STFMR lock-in sweep with Loop > 1: the first two points of every loop are
  discarded, not only of the first. Every loop starts with a jump back to
  start, and each loop now saves the same number of points.
- Gate-VI: mean phase from the mean X and Y (±180° averaged to 0°). The CSVs
  record the SR830's real address.
- PPMS/RT-E: the 6221's output low is floating again after each `*RST`.
- PPMS: a typed temperature rate is kept; below 10 K it is lowered to
  1 K/min. Scan axes no longer repeat their end point. Macro WAITFOR after
  a Driven command no longer takes the old Persistent status.
- PPMS: the live CSV is written before the plots.
- PPMS: the scan confirmation warns before a rotation of more than 30°.
- Magnet scans use the KEPCO's 1 mA resolution, and recovery never enables
  a BOP that was not enabled.
- Plot labels with a 2.5 step at |value| ≥ 1000 keep their half-steps
  (3002.5 printed as 3002).

**Other**
- The Keithley state line says "in use" during a measurement.
- A Keithley that could not be switched off is not blamed on the 6221.
- Gate-VI Measure ticks are held during a scan.
- One failed snapshot no longer skips the rest.
- UI callback errors go to the log (the EXE has no console).
- Queued AVI finalisation is not cancelled at exit.
- The short GPIB spelling `GPIB0::30` shares the lease of `GPIB0::30::INSTR`.
- Removed dead code: the old OpenCV camera loop and `CameraViewport` (about
  400 lines), and two unused helpers.

**Left for the user to decide** (not changed):
- MOKE 6221 post-pulse averaging can include pre-pulse frames when the
  temporal average N > 1.
- The MultiPyVu interface refuses every scan.
- Gate-VI points with no valid reading are averaged from the invalid ones.
- Snapshots capture screen pixels.
- STFMR lows: Connect stays usable during a sweep; the frequency sweep has
  no live CSV; MFLI-only mode keeps unlocked points; a failed PNA RF-off
  is hidden.

## Non-local 2400-IV and Gate-VI: up to ±200 V on a 2400, ±1000 V on a 2410; Set and pulse controls (2026-10-09)

The user asked to raise the 2400-IV voltage limit from ±20 V to ±200 V on a
2400 and ±1000 V on a 2410.

- **Range and OVP:** up to ±20 V the sweep keeps the 20 V range and 20 V
  OVP, as before. Above that (`nonlocal_keithley_source`):
  - the model's high range: 200 V on a 2400, 1000 V on a 2410;
  - the hardware OVP at the lowest level covering the sweep
    (`KEITHLEY_OVP_LEVELS_V`);
  - range and OVP are read back before the output goes on.
- **OVP margin:** the OVP levels are ±5 % (2400-series manual), so above
  ±20 V the level chosen is at least 5 % above the voltages. 50 V takes
  60 V; 160 V on a 2400 takes "none".
- **No OVP above about 152 V on a 2400 and 476 V on a 2410:** those models
  have no OVP level above 160 V / 500 V, only "none" (their 210 V / 1100 V
  maximum). The software limit still stops at the user's ±200 V / ±1000 V,
  and the confirmation says so.
- **Drained before off** (review, 2026-10-09): after high voltage the
  Keithley holds 0 V with the output on before switching it off, so a
  capacitive sample or cable is drained through the current limit. Before,
  it went straight from the set voltage to output off.
  - It holds at least `NONLOCAL_KEITHLEY_DISCHARGE_S` = 1 s, then until two
    readings in a row are off the current limit (the terminals are then at
    0 V).
  - After at most `NONLOCAL_KEITHLEY_DRAIN_MAX_S` = 30 s it switches off
    and reports that the sample may still be charged. A low Current limit A
    drains slowly.
  - It applies to every switch-off (Stop, OFF, end of sweep, failure) and
    before the `*RST` that starts the next measurement.
  - An output found on at a setup, e.g. after a reconnect, is drained too.
- **Every pulse drained** (user, 2026-10-09): Gate-VI pulses and Apply
  pulse end at 0 V with the output on until drained, at any voltage. Before,
  only pulses above ±20 V did. This adds about 1.2 s per point; the
  estimate includes it.
- **Set left on, then another measurement** (review round 2): every
  measurement worker first drains and switches off a live Keithley output
  and reads it back off (`_nonlocal_keithley_off_first`). This happens
  before its Stop check and before the 6221.
  - The state line says "ON, switching off…" until then, and OFF only
    after the read-back.
  - If the read-back fails it says "check the output".
  - Before, it said OFF as soon as the next measurement was queued, even if
    that measurement stopped before reaching the Keithley.
- **Pulse setup read back:** `setup_voltage_mode` now reads back the 20 V
  OVP and 20 V range, so a Keithley left on the high range is caught.
- **Current limit:** at most 0.1 A, and at most 0.021 A on the 2410's 1000 V
  range.
- After the sweep, or on a failure or Stop, the Keithley goes back to the
  pulse setup (20 V range, 20 V OVP, output off).
- **Gate-VI pulses** use the same limits (user, 2026-10-09). Each pulse sets
  its own range and OVP, read back with the output still off
  (`_set_source_range_locked`); a ±20 V pulse goes back to the 20 V range.
  Above ±20 V the start confirmation warns about high voltage.
- **The Keithley on its own** (2400-IV settings, user 2026-10-09):
  - `2400 V` + **Set 2400** leaves the output on at that voltage
    (`set_dc_voltage`) until **2400 / 6221 OFF** or the next measurement
    (each starts from off, and Set 6221 switches the Keithley off first).
  - `Pulse V` + `Width s` + **Apply pulse**: one pulse, output off before
    and after, back in the pulse setup.
  - Both use the Current limit A and the safety tick, switch the 6221 off
    first, and ask a HIGH VOLTAGE question above ±20 V. A line under them
    shows the Keithley's state.
- **Ramp to Start V** (user, 2026-10-09): the sweep goes from 0 V to Start V
  in equal steps of at most `NONLOCAL_IV_SWEEP_RAMP_STEP_V` = 5 V,
  `NONLOCAL_IV_SWEEP_RAMP_DWELL_S` = 0.2 s apart (`ramp_levels`), before the
  first point. That is 25 V/s, so −1000 V takes 40 s. The ramp levels are
  not measured. The confirmation and the time estimate include it, and Stop
  works during it. At the end the output goes to 0 V and is drained, as
  before.
- **Defaults** (user, 2026-10-09): Start −50 V, End 50 V, Step 5 V,
  Settle 0.5 s (21 points; the 200 V range on a 2400).
- **No Sample box** any more (user, 2026-10-09); run folders are
  `2400-IV_<date>_<time>`.
- Tests: `SweepControllerTests` and `SweepValuesTests` in
  `test_nonlocal_keithley.py`.

## Non-local Gate-VI: |R| measured for 15 s, the last 5 s averaged (2026-10-09)

The user asked for 15 s per |R|, averaging the last 5 s
(`NONLOCAL_VI_RES_HOLD_S` = 15, `NONLOCAL_VI_RES_AVERAGE_S` = 5; was 5 s and
1 s). On |R| vs time the reading settles in about 10 s. The hold counts from
the last current change, so with autorange steps a measurement takes a little
longer. Each point now takes about 10 s more; the estimate includes it.

## Non-local Gate-VI: |R| measured as on |R| vs time, about 5 s each (2026-10-09)

The user found that Gate-VI's |R| (1–7 × 10⁸ Ω, scattered) did not match
|R| vs time on the same sample (939 MΩ). They allowed about 5 s per
measurement.

- **Cause:** both use the same Keithley set-up and the same current steps.
  Gate-VI averaged 5 readings in the first ~1 s after the current switched
  on. The sample charges up after each current change, and |R| vs time shows
  it rising to its settled value over about 5 s. Gate-VI therefore read the
  start of the charge-up: low and variable.
- **Now** (`NonLocalVI2410Controller.measure_resistance`):
  - The current is chosen and kept between 0.05 and 2 V as on |R| vs time,
    checked at every reading.
  - Once it stops changing, it is held `NONLOCAL_VI_RES_HOLD_S` = 5 s, and
    the readings of the last `NONLOCAL_VI_RES_AVERAGE_S` = 1 s are averaged.
  - A current change starts the 5 s again.
  - Still changing after `NONLOCAL_VI_RES_MAX_S` = 30 s, the measurement
    fails and the scan stops with the outputs off. This reading is the pulse
    gate.
- **Time:** about 5 s per |R| (was about 1 s); the estimate includes it.
- **Limit:** with the time constant seen on |R| vs time (about 1 s), the 5 s
  value is within about 1–2 % of the settled one. A slower sample needs a
  longer hold.
- Tests: `ResistanceTests` in `test_nonlocal_gate_vi_harmonics.py`, with a
  stand-in sample that charges up.

## Non-local Gate-VI: SR830 1+2, Zurich 1+2 and 1+2+3; Pulse gate 100 MΩ (2026-10-09)

The user asked for SR830 1+2 and Zurich 1+2 / 1+2+3 harmonic choices on
Gate-VI, and a Pulse gate default of 100 MΩ.

- **Choices:** the harmonic boxes are lists, held while a scan runs.
  The Zurich defaults to 1+2 (user, 2026-10-09).
  - SR830: 1, 2, 3, 1+2.
  - Zurich: 1, 2, 3, 1+2, 1+2+3.
- **SR830 1+2:** an SR830 reads one harmonic at a time. At each point it
  reads 1ω for Measure s, switches to 2ω (read back) and waits
  max(Settle s, 10 τ; τ is 1 s on Gate-VI), then reads 2ω for Measure s.
  It then goes back to 1ω for the next point. Each order starts on the
  sensitivity it last ended on, not SENS 9. This adds about
  10 s + Measure s per point (60 s with the default 50 s).
- **Zurich 1+2 / 1+2+3:** all orders at once, as on PPMS and 6221-IV. The
  reference is on Demod 4, the orders on Demods 1-3, and unused demods are
  off. This needs MF-MD; without it the scan stops at configure.
- **CSV:** `VI_raw.csv` and `VI_summary.csv` have one row per meter and
  order; the `harmonic` column is the order.
- **Plots:** one X / Y / θ row per order measured. Below them: the 2400
  |R| and one plot per order against the pulse voltage. The 2400
  command-voltage plot was removed (user, 2026-10-09). Snapshots are named
  by order (e.g. `2nd_harmonic_X_vs_time_snapshot.png`).
- **Colours** (user, 2026-10-09): a colour family per harmonic order, in
  the time rows and the plots against the pulse voltage alike
  (`NONLOCAL_VI_ORDER_COLORS`): 1st blue, 2nd red/orange, 3rd green, with
  shades for the Zurich, SR830 #8 and #9. |R| is purple, the 2182A grey.
- **|R| plot:** only the readings after each pulse. The check before the
  first pulse used to be drawn at the first pulse voltage, which gave two
  points at 20 V; it is now left out of the plot (user, 2026-10-09) and kept
  in `VI_resistance_gate.csv` with stage `initial_gate`.
- **Against the pulse voltage** (user, 2026-10-09): the 1st harmonic plots
  the mean X, the 2nd the mean Y, and the 3rd the mean X (odd, as the 1st);
  `NONLOCAL_VI_SUMMARY_CHANNELS`. The 2182A plots its DC voltage. The CSV
  keeps mean X, Y and R for every order.
- **Pulse gate MΩ** defaults to 100 (was 20).
- **Measure/pt s** defaults to 50 (was 100).
- **Also fixed:** "SR830 #n is selected for the scan but is not connected"
  was shown as "parameters must be valid numbers".
- Tests: `Other-files/test_nonlocal_gate_vi_harmonics.py` and
  `GateVIPlotTests`.

## Non-local Zurich set up as the PPMS module's (2026-10-09)

The user asked whether the Zurich should use 10 MΩ or 50 Ω, and for it to
be set up as on PPMS.

- **Already the same:**
  - Signal Input 1 at 10 MΩ, scaling 1. PPMS/RT-E, 6221-IV and Gate-VI all
    use this. 10 MΩ measures a voltage without loading the sample; 50 Ω would
    draw current through the voltage contacts.
  - AC coupling, R, the low-pass filter order 2 (`NONLOCAL_LOCKIN_FILTER_ORDER`;
    this line said "4th-order" until 2026-10-13), and Ref. and TC from the
    Zurich row.
- **Reference demodulator:**
  - **Before:** for a single harmonic, Non-local used the Zurich's default
    routing, with the reference PLL on Demod 2.
  - **Now:** `NonLocalACController.configure` puts the reference on Demod 4
    wherever the Zurich has MF-MD, and switches the demodulators not
    measuring off, as PPMS/RT-E do (`external_reference_demodulator=3`,
    `disable_unused_ppms_demods`). This covers Gate-VI, 6221-IV and Set 6221.
    The rack's set-up does the same.
  - **A Zurich without MF-MD** keeps its own routing; PPMS's way cannot run on
    it.
- **Transfer rate:** the 6221-IV Zurich is now 1 Hz (10 Sa/s), as PPMS/RT-E;
  it was 25 Sa/s. A reading every 0.2 s is at most 0.1 s old.
- **One difference kept:** the 2nd and 3rd harmonic demodulators run at the
  same 10 Sa/s, not PPMS's 1 Sa/s. 6221-IV averages the Zurich over Acquire s,
  while PPMS takes one snapshot a point.

## Non-local Gate-VI: no Read/Coupling choice; plots X, Y, θ then 2400 and V vs pulse (2026-10-09)

- **Settings (user request):** the Read and Coupling lists are gone.
  - **Coupling:** every lock-in reads AC-coupled.
  - **Voltage against the pulse voltage:** each lock-in's R. These are the
    variables' defaults, kept.
  - **Each row** is now harmonic, Measure and autorange.
  - **Zurich input row:** removed (user request). Signal Input 1 is 10 MΩ,
    scaling 1, as on 6221-IV.
- **Plots, first row:** X, Y and θ against time.
  - **Meters:** every meter ticked Measure is overlaid in its own colour, with
    a legend when there are two or more, and named in the title, as on
    6221-IV.
  - **2182A:** its DC voltage goes with X, since it has no Y or θ.
- **Plots, second row:**
  - the Keithley 2400 command voltage;
  - the 2400 resistance against the pulse voltage;
  - each meter's mean voltage against the pulse voltage.
- **Gone:** the separate 6221 current-readback plot and the per-meter rows.
  The 6221 current stays in the CSVs.
- **Time rows** now carry X and Y: (elapsed, meter, R, θ, kind, X, Y).
- **Snapshots:** each scan's run folder holds a snapshot of all six plots.
- **Tests:** `GateVIPlotTests` in `test_nonlocal_review.py`.

## Non-local Gate-VI: settings laid out as 6221-IV (2026-10-09)

- **Request:** the user asked for the "Gate-VI settings" box to look like
  "6221-IV settings".
- **Left side:** the safety tick on top; then 6221 mA, Frequency Hz and
  Compliance V; then Trigger line and Output (AC/DC).
- **Right side:** one row per lock-in: "SR830 #8 / SR830 #9 / Zurich
  harmonic", Read (X/Y/R), Coupling (AC/DC), **Measure** (was "In scan"), and
  the autorange. The Zurich row shows its input range, as on 6221-IV.
- **Last two rows:** "Zurich input" (impedance and scaling), then "2182A
  Measure (channel 1, NPLC 1, autorange)".
- **Layout:** the two sides sit side by side when they fit, else stacked
  (`_arrange_pair_by_width`), as in 6221-IV.
- **Unchanged:** every setting, its variable and the scan itself.
- **Tests:** `test_panel_defaults.py` checks the Measure ticks, the safety
  tick and the Output list.

## MS-fourState.py: states C and F (2026-10-09)

- **New states (user request):** C (270°, 0 Oe, −23 mA) and F (270°, 0 Oe,
  +23 mA). They are written by the current pulse alone, at a Driven 0 Oe
  setpoint, and are added below D, A, B and E in the States table.
- **Colours:** C orange, F teal.
- **Default cycle and sequence:** unchanged. C and F run where typed, e.g.
  `C F C F`.
- **Tests:**
  - the defaults test;
  - a C F run (pulses −23 and +23 mA at 0 Oe and 270°, read as C and F);
  - the simulated sample takes C or F at 270° with no field, by the
    current's sign.

## MS-fourState.py: the plot leaves out each read's first 5 s (2026-10-09)

- **Why:** on the lab PC each read's SR830 X settled for a few seconds after
  its first sample (the hooks at the start of each read). The user asked for
  the first 5 s not to be shown.
- **What changed:** `StatePlot.shown_samples` draws each read period's X
  samples from `HIDDEN_READ_START_S` = 5 s after its first sample on. The
  axis scales to the samples drawn.
- **What did not change:** the trace CSV keeps every sample, and the run
  CSV's X, Y means are unchanged.
- **CI screenshots:** the example reads are now 15 s with a settling start.

## MS-fourState.py: state E and the read time from the user's figures (2026-10-09)

- **States (`DEFAULT_STATES`):** D 360°/+1500 Oe/+22 mA, A 270°/+1500 Oe/−22 mA
  and B 270°/+1500 Oe/+22 mA are as before. E is now 360°/−1500 Oe/+22 mA (was
  +1500 Oe/−22 mA).
- **Read time:** the default is now 15 s (was 3 s).
- **Tests:** the defaults test, the pulse order and the action order follow
  them; the CI screenshot's example uses the new E.

## MS-fourState.py: PPMS address 192.168.137.137 by default (2026-10-09)

- **Default address:** `PPMS_HOST = "192.168.137.137"` (user request).
- **The list:** the main program's two addresses follow it (`PPMS_HOSTS`).
- **Not affected:** the main program's own default (`PPMS_DEFAULT_HOST`,
  used by the PPMS and RT-E pages) and the copied instrument code are
  unchanged.

## MS-fourState.py opens maximised (2026-10-09)

- **What changed:** the window fills the screen when the script starts (user
  request).
- **How:** `fill_screen` uses `state("zoomed")` on Windows (the lab PC) and
  macOS, and `-zoomed` on X11. Where neither is offered, it sets the window
  to the screen's size.
- **Un-maximised size:** 1720 × 1240, as before.
- **Opting out:** `FourStateApp(root, mode, maximise=False)`. The CI layout
  check uses this, since it checks fixed sizes.
- **Tests:** `FillScreenTests` covers all three ways.

## MS-fourState.py: saving as the PPMS page; Cycles in Sequence mode (2026-10-09)

- **Saving (user request: "change the file save logic as main PPMS"):**
  - **Before:** a fixed folder, `MS-fourState data/<time>_<mode>/`.
  - **Now:** Start asks for a folder and then a file name, as the PPMS page
    does. The default name is `MS-fourState_<Cycle|Sequence>_<time>`; the
    PPMS page's Windows-name check applies, and `_01`, `_02`, … are added so
    nothing is overwritten.
  - **The live CSV** is utf-8-sig, and each row is appended, flushed and
    fsynced (`append_live_csv`).
  - **Save CSV as** saves all rows again.
  - **The other files** take the CSV's name, in the same folder:
    `_settings.json`, `_trace.csv`, `_log.txt` and `_plot.eps`. **Save plot**
    asks for a file. `manual_log.txt` stays in `MS-fourState data/`.
- **Cycles in Sequence mode (user request: "add a cycle number input in the
  run module"):** Sequence mode has its own **Cycles** box, default 1. The
  typed states run that many times, and the summary reads "Run this sequence
  N times?". Cycle mode is unchanged.
- **Tests:** `SaveTests` in `test_ms_four_state.py` covers the folder and
  name dialogs, the name check, the numbered names, the live rows and Save
  CSV as; the mode test now covers the Sequence Cycles box.

## MS-fourState.py runs on its own (2026-10-09)

- **Why:** on the lab PC, `MS-fourState.py` copied alone into
  `D:\user data\Zhang Cheng` stopped with "Transport_all_NH_dev.py must be in
  the same folder". The user asked for it to stand alone.
- **What changed:** it no longer loads the main program. The instrument code
  it uses is copied into the file, between its `BEGIN` and `END` lines:
  - the controllers: `Keithley6221Controller`, `SafeVisaIdentityController`
    (SR830), `PPMSRemoteReadbackController`, the QDInstrument and MultiPyVu
    back ends, and `PPMSMotorDebug` (the rotator);
  - the VISA manager and instrument leases;
  - the PPMS and 6221 constants;
  - `short_status_error`;
  - two methods of the main page, as the functions `ppms_field_is_ready` and
    `read_ppms_cryogen_display`.

  In all, about 2,100 lines. The copy is chosen by following the names the
  program uses through the main program's top-level definitions.
- **`core` names unchanged:** `core` is now the module itself, so
  `core.<name>` still works, and the echoed VISA manager the console installs
  is the one the copied controllers use.
- **Refreshing the copy:** `Other-files/tools/ms_fourstate_copy_core.py`
  rewrites it from `Transport_all_NH_dev.py`.
- **Tests:** in `test_ms_four_state.py`, `StandaloneTests` checks that the
  file does not load the main program, and that the copy matches the main
  program (so a fix there is not forgotten here). The program was also loaded
  alone in an empty folder.

## Non-local: fixes from a review of the whole module (2026-10-08)

Five reviewers read the Non-local code: the instrument controllers, |R| and
2400-IV, Gate-VI and the connections, 6221-IV and the page, and the latest
Zurich/6221 change. None found a path that switches an output on
unexpectedly. Their confirmed findings are fixed below; the new tests are in
`test_nonlocal_review.py` and `test_nonlocal_6221_iv.py`.

- **Outputs and Stop:**
  - **Stop during a connect:** the red Stop and "6221 / 2400 OFF" now switch
    the outputs off even while an instrument is being connected. Before,
    after Set 6221 they did nothing in that window.
  - **OFF is read back:** `_nonlocal_vi_shutdown_outputs` reads each output
    back off (`confirm_output_off`). "OFF" is shown only after that; a
    failure shows "check them".
  - **Gate-VI shutdown failures:** a failed shutdown at the end of a Gate-VI
    scan is reported beside the scan's own error, not in its place.
  - **Disconnecting one instrument:** disconnecting the Keithley alone no
    longer reports the 6221 as OFF, and the 6221 likewise.
  - **Stop before a source switches on:** the Keithley pulse and the
    resistance current source check Stop just before `OUTP ON`.
- **Keithley:**
  - **Pulse width:** Gate-VI pulses were about 80 ms longer than set, because
    the error check after the voltage write ran inside the pulse. The check
    now counts toward the width.
  - **2400-IV current limit:** read back (`:SENS:CURR:PROT?`, within 1 %) and
    used to mark the points at the limit.
  - **Errors:** when the measurement fails and the return to the pulse setup
    also fails, both errors are named.
  - **Stop before the start:** a Stop before the Keithley was touched now says
    so and changes no output label.
  - **Status and log:** a failed 2400-IV sweep names its run folder, and the
    parameter log's start time is when the run folder is made.
- **Zurich:**
  - **Waiting:** scans wait only for a Zurich they set up (`mfli_in_scan`). A
    Zurich that is only connected cost Gate-VI up to 5 s a point, even in DC
    mode.
  - **Autorange:** `sync()` comes before polling, as `zhinst.utils` does it.
    An input overload ranges up and spoils that reading, and with no raw
    extrema a small R never ranges down (the STFMR rules).
  - **Legacy bridge:** replies carry the request id, so a late reply is never
    taken for the next request's.
  - **Demodulators:** the ones disabled follow the reference routing; any
    other reference PLL is switched off on configure; disconnect disables
    the demodulator actually used for the reference.
  - **Stop during the reference wait:** this no longer raises `KeyError:
    'lia_status'`.
  - **The rack row:**
    - The Zurich list is held while one is connected, and Ref., TC and the
      6221-IV lock-in choices are held while a measurement runs.
    - The Ref. follows another Zurich only while none is connected.
    - "Locked" is cleared when a measurement starts.
    - Without MF-MD the rack shows the 1st-harmonic lock.
- **SR830:**
  - **Overload after a range change:** after a sensitivity change, the next
    LIAS? is read once and dropped, as on PPMS/RT-E. The change itself
    latches the overload bits.
  - **Unreadable status:** an unreadable `*ESR?` after a setting now sends
    the setting again, instead of hiding a rejection.
- **6221-IV, as RT-E:**
  - **Settle after a range step:** 5 τ of that SR830, or 10 τ of the Zurich.
    Ranging longer than 180 s fails the point.
  - **First point of each pass:** windows are repeated until two agree (at
    most 10). The count goes in the new CSV column `settle_windows`.
  - **Plot:** one line per loop, one legend entry per meter, and the legend
    is drawn above compact plots.
  - **CSV:** the column `in_compliance` is renamed `compliance_readings`; it
    holds a count.
  - **Time estimate:** includes each pass's first-point settle.
- **A second review of these fixes:**
  - **What OFF claims:** OFF is claimed only for an output actually read back
    (`_nonlocal_vi_shutdown_outputs` returns them). An instrument that is
    not connected keeps the state it was reported in.
  - **Busy page:** the page stays busy until a queued OFF has run, even when
    a connection finishes first. Pending OFFs are counted, so a second press
    is covered too. A "check it" warning about an instrument that was not
    read back stays visible.
  - **Disconnects:** a disconnect says "OFF sent", because it cannot read
    the output back.
  - **Endless ranging:** a 6221-IV point that keeps ranging now fails by the
    clock or after 30 steps in a row (`NONLOCAL_IV6221_MAX_RANGE_STEPS`).
    Before, an input overloaded above its top range could hold the 6221 on
    for minutes.
  - **Failed shutdown after Stop:** this again opens a dialog.
  - **Stop before a switch-on:** this takes back the "ON" text.
  - **MF-MD before the first configure:** found as `configure_mfli` finds it
    (`NonLocalACController.mfli_has_multi_demod`).
  - **Legacy bridge:** switches off any other reference PLL too.
  - **Pulse error check:** the check of the pulse level now comes after the
    pulse, so short pulses keep their width as well. A rejected level only
    left 0 V on, and it is switched off the same way.
- **Not changed, for the user to decide:**
  - **Keithley output-off state:** the Keithley's "output off" is the 2400's
    NORMal mode (0 V, low compliance), not an open relay. `:OUTP:SMOD HIMP`
    would make the gate float while the meters read. That is an instrument
    setting, not changed without approval.
  - **Dead code:** the old `nonlocal_*` state from an earlier page, and the
    unreachable connect-all/disconnect-all functions, are kept for a separate
    cleanup.

## Non-local Zurich as on RT-E: Ref. by Zurich, live lock, continuous 6221 sweep (2026-10-08)

On the lab PC, the Zurich (DEV4414) never locked in a Non-local 6221-IV scan
("Lock-in reference not locked at 17.3 Hz: Zurich"). On RT-E, with the same
cables, it showed "Locked". The two pages differed in three ways:

- **Ref.:** RT-E has DEV4414 on Trigger In 1, while the Non-local rack had
  Aux Input 1.
  - **Ref. follows the Zurich:** choosing a Zurich now sets its wiring
    (`NONLOCAL_MFLI_DEVICE_REFERENCE_INPUTS`). DEV4414 (Super) goes on
    Trigger In 1, as on PPMS and RT-E. DEV4435 (Normal) goes on Aux Input 1,
    as on STFMR and Gate-VI. The Ref. can still be changed by hand.
  - **Default:** the rack now starts on RT-E's Zurich, DEV4414 with Trigger
    In 1.
- **Lock state:** RT-E sets the Zurich up when it connects and reads it once
  a second, so its row says "Locked" or "Warning". Non-local did neither, so
  its row could only say "Connected". Now:
  - **On connect:** the Zurich is set up as RT-E does it, with Ref., TC and
    the 6221-IV Zurich harmonic (`_apply_nonlocal_mfli_config`). If set-up
    fails, the Zurich stays connected and the status says why.
  - **On a change:** a change of Ref., TC or the Zurich harmonic is applied
    at once.
  - **While idle:** the row reads the lock once a second
    (`_poll_nonlocal_mfli`, `NONLOCAL_MFLI_POLL_MS`) and shows "Locked",
    "Warning" or "Read failed". The 6221-IV Zurich row shows the Signal
    Input range, e.g. "Autorange 10 mV". Reads pause while a scan,
    connection or task runs.
- **The 6221 between points:** 6221-IV aborted and restarted the sine at
  every point (`set_current`: ABOR, AMPL, ARM, INIT). That stops the Trigger
  Link marker the lock-ins lock to. Now 6221-IV runs as the RT-E Current
  scan:
  - **Range:** `configure(sweep_peak_a=…)` sets one fixed range for the
    largest current (`SOUR:CURR:RANG`, `SOUR:WAVE:RANG FIX`,
    `SOUR:WAVE:DUR:CYCL INF`).
  - **Points:** the sine starts once, and after that
    `NonLocalACController.update_amplitude` changes only `SOUR:WAVE:AMPL`,
    read back with the output checked on. This continues through every
    harmonic pass.
  - **Set 6221 and Gate-VI:** these keep the automatic range.
- **Lock wait:** at the start of each pass the scan waits up to
  `NONLOCAL_IV6221_REFERENCE_WAIT_S` (20 s) for every lock-in to lock, and
  shows "waiting for the reference lock (…)". An unlocked Zurich's message
  names its Ref. input.

## Non-local: RT-E-style settings and scan boxes, the rack's Zurich Ref./TC, run folders (2026-10-08)

- **Settings and scan boxes:** 2400-IV, 6221-IV and Gate-VI each have a
  "… settings" box (the instruments) and a "… scan" box (the sweep, the
  buttons and the readouts), as on RT-E. The two sit side by side when both
  fit and are stacked otherwise (`_arrange_pair_by_width`). Gate-VI's meter
  read settings are kept in one column, so its two boxes sit side by side
  in the 2000 px window.
- **Rename:** the "Gate-VI pulse scan" sub-tab is now "Gate-VI scan".
- **Zurich row:** as on RT-E, the rack's Zurich row has **Ref.** and
  **TC (s)** after its status.
  - **Ref.:** the reference input, moved from the Gate-VI read settings
    (default still Aux Input 1).
  - **TC (s):** `nonlocal_mfli_tc_var`, default 0.1 s (1 s since
    2026-10-14, see the top section), used by 6221-IV and Gate-VI.
  - **Change for Gate-VI:** its Zurich TC was a fixed 1 s and now follows
    the rack (0.1 s by default then; 1 s since 2026-10-14). Its SR830s keep 1 s.
- **6221-IV as RT-E:** the STFMR-style Meter list is replaced by RT-E's
  layout and start-up values (`NONLOCAL_IV6221_*`).
  - **Settings:** 0.1 mA RMS, 17.3 Hz, 50 V; Set 6221; Trigger line;
    "6221 / 2400 OFF".
  - **Lock-ins:** each has a harmonic mode (1, 2, 3, 1+2 or 1+2+3), a
    Measure tick and "Autorange ON". By default only the Zurich measures,
    at 1+2+3.
  - **Scan:** 0.01 to 1 mA RMS in 0.01 mA steps, Loops 1, Settle 0.5 s,
    Acquire 1 s.
  - **Harmonic passes:** an SR830 at 1+2 or 1+2+3 runs the sweep once per
    order. `NonLocalACController.set_lockin_harmonic` sets the next order
    and reads it back. The reference lock is checked at the start of each
    pass, and an SR830 drops out once its orders end.
  - **Zurich orders:** the Zurich reads all its orders in every pass, as on
    PPMS/RT-E. Demod 4 takes the external reference, and the orders are on
    Demods 1–3 (`configure_second_harmonic`, `configure_third_harmonic`,
    `disable_unused_ppms_demods`, `read_mfli_four_demods`). Its curve is
    drawn from the first pass.
  - **SR830 time constant:** the front-panel one is kept, as on RT-E and
    PPMS (`configure(..., sr830_front_panel_time_constant=True)` leaves out
    OFLT/OFSL). It is read back with OFLT? (`SR830_TIME_CONSTANTS_S`).
    Gate-VI still writes 1 s.
  - **Far jumps:** the first point of each pass, and the jump back to Start
    at a new loop, wait at least 10 time constants of the slowest lock-in.
  - **Plots:** one X / Y / θ row per measured order, with the meters
    overlaid in the PPMS colours. Rows are hidden for orders nobody
    measures. As on RT-E, each title names its meters ("2nd harmonic θ —
    Zurich"); a legend appears only when two or more share a plot.
  - **Zurich with one order:** this needs no MF-MD. It uses Gate-VI's
    reference routing, as the old 6221-IV did. Only 1+2 and 1+2+3 put the
    reference on Demod 4.
  - **Extra Zurich orders:** these run at Demod 1's transfer rate (25 Sa/s)
    rather than PPMS's 1 Sa/s, so a reading every 0.2 s never comes from
    before the current step. `configure_second_harmonic` and
    `configure_third_harmonic` take an optional `rate`, and PPMS is
    unchanged.
  - **SR830 at a new pass:** it restarts at the range `configure()` starts
    at (SENS 9, read back), because the next order starts at Start current.
  - **CSV:** `harmonic_pass`, then the SR830 columns (with the harmonic of
    that pass) and `Zurich_<n>w_X/Y/R/theta` per order.
- **6221 state after the review:**
  - **Stopping Set 6221:** "6221 / 2400 OFF" pressed while Set 6221 is
    configuring or switching on now leaves the 6221 off, and reads that back.
  - **The "6221: …" label:**
    - |R| and 2400-IV show "6221: OFF" only once the 6221 has been read back
      off.
    - If it will not switch off, the label and status name the 6221.
    - A Gate-VI scan, Disconnect all, or disconnecting the 6221 also sets
      the label to OFF.
  - **The rack TC:** it is checked only when the Zurich is ticked Measure.
- **Run folders:** each 2400-IV and 6221-IV run now writes its own folder
  (`_nonlocal_make_run_directory`), as PPMS, STFMR and Gate-VI do.
  - **2400-IV:** `2400-IV_<sample>_<time>/` holds `<folder>.csv`,
    `<folder>_parameters.log` and the PNG from "Save plot PNG".
  - **6221-IV:** `6221-IV_<time>/` holds the CSV and the log.
- **Tests:**
  - `test_nonlocal_6221_iv.py`, rewritten: passes, Zurich orders, settle,
    parse, run folders, `set_lockin_harmonic`, Set 6221, plots.
  - `test_nonlocal_keithley.py`: the 2400-IV run folder.
  - `test_panel_defaults.py`: tab names, RT-E defaults, the settings/scan
    boxes, the Zurich row.
  - `ci/gui_checks.py`: example data with the Zurich at 1+2+3.

## Non-local 2400-IV: choose the folder at Start and save a live CSV, as 6221-IV does (2026-10-08)

- **Start:** "Choose folder and start 2400-IV sweep". The Yes/No summary
  comes first, then the folder dialog.
  - **Files:** `2400-IV_<sample>_<time>.csv` and its `_parameters.log` are
    created there. The log holds the sample, model, resource, identity,
    sweep settings, NPLC, range and OVP.
  - **During the sweep:** the worker opens the CSV before the Keithley is
    switched on and writes each point as it is read (point, set and source
    voltage, current, elapsed time, at the limit). A stopped or failed sweep
    keeps its points.
- **Save CSV + PNG → "Save plot PNG":** this button now writes the plot as
  shown, `<csv stem>.png`, beside the CSV. A screen capture needs the plot
  on screen, so this stays a button rather than happening at the end.
- **Status:** the finish message names the CSV, or says none was written.
  The "previous sweep not saved" warning is gone, because nothing is
  unsaved any more.

## Non-local 6221-IV: lock-in settings and layout as on the STFMR page (2026-10-08)

- **Layout:** like the STFMR field-sweep box. One aligned table with label:
  field pairs sits on the left, four pairs to a row so it fits the 1101 px
  window. Its rows are:
  - Start / End / Step mA RMS, Loops;
  - Frequency, Comp. V, Settle s, Acquire s;
  - **Meter**, Coupling, Trigger line, Round trip;
  - the SR830 #8, SR830 #9 and MFLI harmonics, MFLI TC s;
  - MFLI reference, MFLI autorange;
  - the safety tick.

  On the right are Start / "Stop / 6221 off", the last point and each
  meter's X, Y and θ, and two lamps: **Ref** (locked) and **Left out**
  (readings dropped as unlocked, overloaded or autoranging). The progress
  bar sits under the box, then the X, Y and θ plots.
- **Meter list:** one Meter list (`NONLOCAL_IV6221_METERS`) replaces the
  per-meter "In scan" ticks. Its choices are SR830 #8, SR830 #9, both, the
  MFLI, or the MFLI with either or both SR830s. The default is SR830 #8 + #9,
  with harmonics 1, 2 and 2 (MFLI).
- **Own settings:** 6221-IV now has its own lock-in settings. It no longer
  shares Gate-VI's read settings; that second copy is removed.
- **MFLI options:** the MFLI reference input, time constant (default 1 s)
  and input autorange now reach the instrument.
  `NonLocalACController.configure` passes `mfli_config["timeconstant_s"]` to
  `configure_mfli`, and `mfli_config["auto_range"]` sets
  `mfli_auto_range_enabled`. Gate-VI keeps autorange on and the 1 s TC. The
  MFLI input is Signal Input 1, 10 MΩ, scaling 1, recorded in the log.
- **SR830 options left out on purpose:**
  - **REF IN:** not offered, because the reference is the 6221 Trigger Link
    TTL, read on the falling edge.
  - **Autorange tick:** not offered, because this controller starts the
    SR830 at 2 µV full scale, so its sensitivity autorange stays on.

## Non-local page: 6221-IV (the RT-E Current scan); I–V sweep renamed 2400-IV (2026-10-08)

- **Sub-tabs:** |R| vs time, **2400-IV** (the Keithley I–V sweep, renamed),
  **6221-IV** (new), Gate-VI pulse scan.
- **What 6221-IV does:** the RT-E Current scan, run on the Non-local page's
  own 6221, SR830 #8/#9 and MFLI, so nothing is connected twice. It uses
  `nonlocal_vi_ac_controller`, the controller the pulse scan uses.
  - **Plan:** `rte_scan_plan("Current", …)` gives Start → End in mA RMS, with
    Loops and an optional round trip, as on RT-E.
  - **Each point:** `set_current` sets the sine. After Settle s, the meters
    ticked "In scan" are read every 0.5 s for Acquire s
    (`_nonlocal_iv6221_read_point`).
    - **Left out:** readings flagged unlocked, overloaded or in compliance.
      They are counted in `<meter>_discarded`.
    - **Autorange:** a step restarts the window, as on RT-E.
    - **Failure:** a point fails if a meter has no valid reading within
      Acquire s + 30 s, or if the 6221 reports compliance on three readings
      in a row.
  - **R and θ:** computed from the mean X and Y, as on RT-E. A mean of
    phases near ±180° would come out near 0°.
  - **First point:** a reference that is not locked stops the scan, with a
    pointer to the Trigger Link line and cable.
  - **Longer settle:** the first point, and the first point of each loop
    without a round trip, wait at least 10 s (10 time constants), because
    the current jumps there from far away.
  - **Defaults:** RT-E's current range, 0.01 → 1 mA in 0.01 mA steps; 17.3 Hz;
    50 V compliance. Settle 5 s and acquire 2 s, because the page's lock-in
    setup uses a 1 s time constant (RT-E's 0.5 s / 1 s would read
    unsettled values here).
- **Meter settings:** the read settings box is built a second time
  (`_build_nonlocal_vi_read_settings(gate=False)`), bound to the same
  variables. A change on either sub-tab shows on both. The Trigger Link
  line is shared too. This copy leaves out three things 6221-IV does not
  use:
  - the 6221 AC/DC choice;
  - the 2182A;
  - the Read choice, because 6221-IV records X, Y, R and θ.

  The 6221-IV safety tick clears on any instrument connect or disconnect.
- **Plot legends:** the next legend entry starts after the label as drawn
  (`canvas.bbox`). The per-character estimate let "SR830 #8 1ω" run into the
  next entry in the lab's Windows font.
- **Before the scan:**
  - a Yes/No summary;
  - a choice of folder;
  - `6221-IV_<time>.csv` and `_parameters.log` are written there.
- **During and after:**
  - One row per point is written to the CSV: currents, frequency, and for
    each meter its harmonic, X, Y, R, θ, sample and warning counts, and
    compliance.
  - X, Y and θ are plotted against the current, one coloured series per
    meter, using `_draw_nonlocal_series`.
- **Safety:**
  - Only one of |R|, 2400-IV, 6221-IV and the pulse scan runs at a time
    (`nonlocal_keithley_task`).
  - A connected Keithley is checked off before the scan and at each point.
  - The 6221 is switched off and checked off afterwards, whether the scan
    finished, was stopped or failed.
  - The red button stops it.
- **Tests:** `test_nonlocal_6221_iv.py` covers:
  - the steps and the CSV;
  - loops and the round trip;
  - Stop and a failed read leaving the 6221 off;
  - the Keithley check;
  - refused settings;
  - finish messages and the plots.

  `test_panel_defaults.py` checks the sub-tabs, the defaults and the shared
  settings.

## Non-local page: a Keithley 2400 by default (2026-10-08)

The Non-local page now starts with **2400** chosen in its Model list
(`NONLOCAL_DEFAULT_KEITHLEY_MODEL`). This covers the |R|, I–V and Gate-VI
labels, messages and the red button text. The 2410 can still be chosen
before connecting. MOKE-24 keeps the 2410 (`KEITHLEY_DEFAULT_MODEL`).

Both models are driven the same way on this page: 20 V range, 20 V OVP, and
the identity is checked against the chosen model at Connect.

## Non-local page: sub-tab order (2026-10-08)

The sub-tabs are now in the user's order: **|R| vs time**, **I–V sweep**,
**Gate-VI pulse scan**. |R| vs time opens first. `gui_checks.py` shoots them
in that order.

## Non-local page: the connection box in two columns, as on PPMS and STFMR (2026-10-08)

- **Connection box:** like the PPMS page's, it holds only connections, each
  row with its own state, in two columns:
  - **Left:** the Keithley 2410/2400 with its Model, the 6221 and the 2182A.
  - **Right:** SR830 #8, SR830 #9 and the Zurich MFLI.
  - **Narrow window:** the columns stack at the same row spacing, one list
    as on the STFMR page.
  - **Summary line:** gone. `nonlocal_vi_connection_var` is still kept up to
    date.
- **Gate-VI read settings:** the pulse scan's own settings moved into its
  parameter box, now "Gate-VI scan parameters and read settings"
  (`_build_nonlocal_vi_read_settings`), as STFMR keeps its meter settings
  with the sweep parameters. They are in two columns: the 6221 and the
  SR830s on the left, the MFLI and the 2182A on the right. The box holds:
  - the 6221 AC/DC output;
  - each lock-in's coupling, harmonic, Read and "In scan";
  - the MFLI reference input, impedance and scaling. These are on one strip
    that ends in a tail column, so the MFLI fields line up with the SR830
    rows;
  - the 2182A "In scan";
  - the Gate-VI safety check.
- **Result:** the |R| and I–V sub-tabs, which do not use these settings, get
  the height back.

## Non-local page: Gate-VI, |R| vs time and an I–V sweep (2026-10-08)

- **The page:** the Gate-VI tab is now **Non-local**. Its instrument rack
  stays at the top. Under it, three sub-tabs (`nonlocal_measurements`) share
  the Keithley 2410/2400. The red stop button sits at the right end of the
  sub-tab strip, so it shows on every sub-tab. In the lab's 1101 px window
  the rack takes about half the height, so nothing else sits between the rack
  and the sub-tabs.
  - **Gate-VI pulse scan:** the old Gate-VI interlock text, settings, run box
    and plots. The Trigger Link line is now in the run box.
  - **|R| vs time:** moved from MOKE-24. It uses the same autorange, 20 s
    window and plot as before. A reading with no current (an open circuit)
    is skipped, as before. It runs on the pulse Keithley
    (`NonLocalVI2410Controller.start_resistance`, `set_resistance_current`,
    `read_resistance`).
  - **I–V sweep:** new, as in the user's 1 µm Pt-Pt figure.
- **The sweep:**
  - **Settings:** Sample, Start V, End V, Step V, Settle s and Current limit
    A. Defaults are −6, 6, 0.25, 0.5 s and 1 mA.
  - **Voltages:** stepped once from Start to End, both included
    (`build_iv_sweep_voltages`). They are limited to ±20 V in the 20 V range.
    `start_iv_sweep` checks that the 20 V OVP reads back before the output
    goes on.
  - **Each point:** the current is read at NPLC 1 after Settle s. The
    voltage column is the source value the Keithley reports. At the current
    limit the sample sees less, and a point at 99 % of the limit or more is
    marked as at the limit.
  - **Plot:** I against V with black squares joined by a dotted line, and
    the sample name in the corner. The unit is pA, nA, µA or mA. Points at
    the current limit are red.
  - **Save CSV + PNG:** a CSV (point, set and source voltage, current,
    elapsed time, at the limit), the plot as a PNG and a parameter log.
- **Safety:**
  - **One at a time:** `nonlocal_keithley_task` holds the Keithley for |R| or
    I–V. Meanwhile the pulse scan, the rack's Connect/Disconnect buttons and
    the model list wait, and a Gate-VI scan or connect blocks |R| and I–V.
  - **6221 first:** before either starts, the 6221 output is switched off
    and checked off, if the 6221 is connected on this page. A 6221 connected
    elsewhere is not checked. The interlock holds its 2410 phase throughout.
  - **Before the sweep:** a Yes/No summary shows the range, the number of
    points, the settle time, the current limit and the estimated time. It
    also names a previous sweep that has not been saved.
  - **Stop:** a Stop pressed before the worker reaches the Keithley switches
    nothing on. Start waits while a save is in progress, so the PNG shows the
    saved sweep.
  - **Afterwards:** `restore_voltage_mode` zeroes the Keithley, switches it
    off and returns it to the pulse setup (20 V, 1 mA, output off). This
    happens whether the measurement completed, was stopped or failed. The
    red button and Stop end either.
- **MOKE-24:** keeps its connection, manual output and voltage scan. Its |R|
  row and plot are gone, so the two plots share the row.
- **Tests:** `test_nonlocal_keithley.py` (replacing
  `test_keithley_resistance_plot.py`) covers:
  - the sweep voltages and the current units;
  - the I–V plot, and the |R| plot without numpy;
  - the Keithley commands (*RST, OVP readback, limit, NPLC, output on at
    0 V) and the switch-off afterwards;
  - stop and errors mid-sweep, and |R| autoranging.

  `test_panel_defaults.py` checks the sub-tabs, the defaults and the
  turn-taking. `gui_checks.py` inspects and screenshots each sub-tab.

## MS-fourState.py: an instrument command console (2026-10-08)

The log beside the plot is now a console like a command-line session. It
shows the program's messages and, with the **Instrument commands** tick,
every command sent to the instruments and every reply, in order:
`6221 > OUTP?`, `6221 < 0`, `SR830 ! VI_ERROR_TMO`.

- **6221 and SR830:** each instrument the shared VISA manager opens is an
  `EchoedResource` (`echo_visa_commands`), so every write, query and read is
  shown, from `*IDN?` at Connect on. The manager stays the same object, so
  its reference count is unchanged.
- **PPMS:** wrapped in `EchoedPPMS`, which shows SetField with its rate,
  approach and end mode, each reading, and connect. The rotator's MOVE and
  GetPPMSItem lines come from the core's logger.
- **Display:** a line repeated unchanged is counted on its line (×N). Sent
  lines are blue, replies grey and failures red, in a fixed-width font on a
  dark background. **Clear** empties it. The last 3000 lines are kept, and a
  run's `log.txt` gets every line.
- **Tests:** `ConsoleTests` covers the echo of writes, queries and failures
  with replies unchanged and settings forwarded, the wrapping of each opened
  instrument, the PPMS lines and the rotator lines. A window check covers
  the counting and the tick.

## MS-fourState.py: a timing diagram on elapsed time; A and E at +1500 Oe, −22 mA (2026-10-08)

- **Plot:** a timing diagram against the time since the run started, which
  replaces the clock-time axis of the entry below. Each period shows at its
  real time and length:
  - **H:** the measured field.
  - **I:** each write pulse as a spike in its state's colour, and the read
    sine as a block at its RMS level while on.
  - **SR830 X:** every sample of each read, lettered by state.
  - The run engine records the timeline (`FourStateRun.trace`). It is also
    saved as `MS-fourState_trace.csv` (time_s, quantity, value, unit, state).
  - The plot is redrawn at most every 0.3 s and thins very long runs for
    drawing.
- **States:** A is 270°, +1500 Oe, −22 mA and E is 360°, +1500 Oe, −22 mA.
  Every state is now written at +1500 Oe, and the current's sign picks the
  state. The tests' simulated sample follows that (sign of H × I).
- **Fixes:**
  - The Manual settings note wraps to its box (the layout check passes).
  - The window tests close the window through the program, which ends its
    timers first.
- **Tests:** `TimelineTests` checks that each pulse comes in the write field,
  that X is read only while the read current is on, and that the timeline is
  in time order. They also check that a manual action makes no timeline.

## MS-fourState.py: clock time on the x-axis (superseded above); compact setup boxes (2026-10-08)

- **x-axis:** the clock time of each point, its CSV `timestamp`, so a
  state's retention can be read off the plot (non-volatile storage). Ticks
  fall at round times, as HH:MM or HH:MM:SS, with the date when the axis
  spans days. The title reads "Local time (date)". This replaces the time
  since start.
- **Compact layout:** the six setup boxes are placed side by side at their
  own widths (`FourStateApp._flow`) instead of each spanning the window with
  blank space. They wrap to a new line only when the window is too narrow.
  The boxes on a line share its height and spare width; on the lab PC's
  1720 px window that is two lines.
  - **Manual settings:** narrower, with Pulse on its own row and the note
    under the rows.
  - **Readings:** the "Now:" line is gone; the PPMS status box shows the
    readings, and the log keeps each one.
  - **Connections:** the DLL field is shorter.
- **Manual pulse width:** the Pulse row has its own **Width µs** field
  (default 30). It does not change the runs' pulse width; the count and
  compliance still come from the Write box.
- **Tests:** `time_ticks` (round clock ticks, seconds on a short axis, the
  date over days), `flow_lines`, and the manual width (used, checked, and
  leaving the runs' width alone).

## MS-fourState.py: PPMS status box, a thinner settings row, time on the x-axis (2026-10-08)

- **PPMS status:** to the right of Connections, as the PPMS page's live
  readings. Temperature, field and angle in large type with their status,
  chamber pressure and helium level, the 6221 output and the time read.
  - Read only, every 2 s while idle; pressure and helium every 30 s, the
    PPMS page's own read.
  - During a run or a manual action it shows that action's own readings, at
    most one a second, so nothing extra is asked of the instruments.
  - In a narrow window it moves under Connections.
- **Settings row:** the Write box is two columns of four, with shorter labels,
  so the States/Write/Read row is about five lines tall instead of nine, and
  the plot gets that height.
- **Plot:** the x-axis is the time since the run started, `time_s` as in the
  CSV, in s, min after 10 min, or h after 3 h, instead of the step number.
- **Tests:** `StatusBoxTests` checks the status read (read only, PPMS and
  6221), the run's readings being shown at most once a second with each
  rotation, the box's text, and that it polls only while idle. A test checks
  the time axis unit.

## MS-fourState.py: one Connect per instrument, a larger plot, no A' and B' rows (2026-10-08)

- **Connections:** the PPMS, the 6221 and the SR830 each have their own row:
  name, address, Connect, Disconnect, a lamp and their state, as on the main
  program's pages. "Connect all" and "Disconnect all" are gone. The lamp is
  grey (not connected), green (connected) or red (the last connect failed,
  with the reason beside it). One instrument connects or disconnects without
  touching the others. An address is fixed while its instrument is
  connected.
- **Plot:** the window opens at 1720 × 1240 where the screen allows (the lab
  PC's 2560 × 1440 does), up from 1360 × 960. The log column is narrower, and
  the plot's numbers, labels and markers are larger.
- **States:** the table lists D, A, B and E only. To write A or B the other
  way (the figure's A' and B'), enter the reversed field and current in its
  row.
- **Tests:**
  - in `Other-files/test_ms_four_state.py`, connecting or disconnecting one
    instrument touches no other, and a failed connect shows its reason and a
    red lamp;
  - a window check covers the buttons;
  - the cloud check adds the 1720 × 1240 window.

## MS-fourState.py: D and E at 360°, not 0° (2026-10-08)

States D and E now default to 360° rather than 0°. This is the same
orientation, so switching to or from A and B at 270° turns the rotator 90°
instead of 270°. The Manual settings Angle box also starts at 360.

If the rotator reads 0° when a run starts, the first move to D at 360° is one
full turn. It is counted as a large rotation in the run's confirmation.

The values in the States table can still be edited before a run. The tests'
simulated sample takes D and E at 0° or 360°.

## MS-fourState.py: manual angle, field and current settings (2026-10-08)

A **Manual settings** box, between Connections and the state table, sets one
thing at a time, like the PPMS settings rows of the main program:

| Row | Fields | Buttons |
| --- | --- | --- |
| Angle | Angle °, Decel | Move, Read |
| Field | H Oe, Oe/s | Set H, Zero H |
| Current | I mA RMS, f Hz; Pulse mA | Set I, I off; Pulse |

- **Move:** only at 0 Oe, with the 6221 turned off first. Turns over 30° ask
  for confirmation.
- **Set H / Zero H:** end Persistent, confirmed by the PPMS page's readback
  rule.
- **Set I:** leaves the read sine on, and shows the SR830 lock without writing
  to the SR830.
- **Pulse:** sends one write with the Write box's width, count and compliance.
- **Read:** shows angle, field, temperature and 6221 output.
- **Shared fields:** Decel, Oe/s and f Hz are the Write/Read boxes' own fields.
- **Safety tick:** every action except I off and Read needs it.
- **Stop:** 6221 off and 0 Oe (Driven). An error turns the 6221 off and
  leaves the field and angle.
- **Safety review:** an adversarial review found no wrong values and no
  overlap with runs. It found gaps, all fixed:
  - **Rotator interlock:** nothing is sent while the rotator is not at rest.
    A stopped Move blocks the other buttons and Start until a Read shows it
    at rest, as the PPMS page's `ppms_motor_pending`.
  - **Move checks:** Move checks the field and the rotator before it turns
    anything off, reads the field again after the large-turn question, and
    is refused after an unconfirmed Set H until Zero H.
  - **Limits:** the field rate and compliance are checked for manual actions
    too.
  - **Set H wait:** Set H shows the field while it waits and ends at once on
    MagnetFailure or CurrentError.
  - **Stop:** a Stop just as an action ends is honoured, and an instrument
    that is not connected is not reported as a shutdown problem.
  - **Dialog:** a question cancelled by Stop is never shown.
  - **Log:** manual actions are logged to `manual_log.txt`.

The run engine and its checks are shared: `_shared_settings` checks the Write
and Read values for runs and manual actions alike. The window opens 60 px
taller (1360 × 960), so the plot keeps its height. Tests:
`ManualTests` (19, simulated instruments), four `DisplayTests` for the
interlocks and the late Stop, and a window check in
`Other-files/test_ms_four_state.py`. Removing the zero-field rule for Move, or
the stale-Persistent rule, fails them.

## MOKE-24 and Gate-VI: laid out like STFMR and PPMS; Gate-VI drives a 2410 or 2400 (2026-10-07)

**MOKE-24 page**, top to bottom:

- **Instrument connection:** the rack row used on STFMR and PPMS. It has
  the name, an 18-character address, Connect, Disconnect, the lamp and
  the state. Model and Current limit sit on the line below, under the
  address. The old single row was cut off at the lab's 1101 px window.
- **Keithley output:** beside the connection when the window is wide
  enough, else under it. It holds the safety check, Manual V
  (Write/Flip/Zero), Step V (↓/↑), the red off button and the output
  readback, as in STFMR's KEPCO output. The safety check moved here from
  the scan box, and it still covers both manual and scan voltages.
- **Hazard text:** full page width, naming the model's limit and OVP.
- **Resistance:** the controls and the four readings only.
- **Voltage scan and ROI editing:** side by side when they fit. The scan
  buttons sit on a row of their own, so the parameter columns stay
  compact.
- **Plots:** all three in one row of equal width: ROI mean vs time,
  Voltage vs ROI mean, and |R| vs time. They take the spare height. The
  |R| plot no longer takes the top of the page. Each canvas asks for
  little width, so a third of the 1101 px window is enough. The "waiting"
  messages wrap inside the plot instead of running past it.

**Gate-VI page:**

- **Model:** the Keithley row has a Model list, 2410 (default) or 2400.
  It is fixed while connected or busy. The 2400 is driven exactly as the
  2410 here: 20 V range, 20 V OVP (a real level on both models) and
  1 mA. The model must match `*IDN?`, using the same anchored check as
  MOKE-24.
  - A failed connect keeps the previous model.
  - The row name, the red button ("Stop 2400 / 6221 now"), the interlock
    text, the plot titles, the status messages and the plot snapshot names
    follow the model.
  - `VI_parameters.log` records `gate_keithley_model` and its identity.
  - The CSV column `2410_output_state` keeps its name, so existing
    analysis still reads it.
- **Rack rows:** every name is right-aligned in one width with an ASCII
  colon; the full-width "：" is gone from this page. Addresses are 18
  characters, and Connect and Disconnect have the STFMR widths. The
  connection box is titled "Gate-VI instrument connections".
- **Unchanged:** the MOKE-24 / Gate-VI interlock. Either page still blocks
  the other while it holds its Keithley, whatever the address.
- **Tests:** in `Other-files/test/test_instrument_safety_logic.py`, the
  Gate-VI controller connects to a fake 2410 and 2400. The tests check:
  - a wrong model is refused;
  - a failed connect keeps the model;
  - re-connecting as the other model is refused;
  - 20 V range and OVP on both.

  `Other-files/test_panel_defaults.py` checks the Gate-VI Model list, the
  labels that follow it, the lock while busy, and that no full-width colon
  is left on the page. The layouts are checked by the CI screenshots.

## MOKE-24: a 2400 keeps a real OVP, at 160 V (2026-10-07)

A review of the 2400 support found that a 2400 has no 200 V OVP level. Its
levels are 20, 40, 60, 80, 100, 120 and 160 V, and any value from 161 to
210 V selects none (2400 user's manual, `:SOUR:VOLT:PROT`). The page asked
for 200 V, so on a 2400 the hardware OVP was silently off. The readback
check let that through, because it allowed 5 % above 200 V (210.2 V) and
"none" reads back as 210 V.

- **2400:** OVP 160 V and a ±160 V software limit, on scans, manual
  voltages and steps. The hazard text and the parameter log give the
  model's limit and OVP. The log also records the OVP read back.
- **2410:** unchanged, ±200 V with a 200 V OVP.
- **Readback check:** the OVP read back must equal the level asked for,
  within 1 % instead of 5 %. A model lacking that level is refused before
  the output turns on. A 2400 driven as a 2410 is now caught at this step.
- **Connecting:** the Model list and Connect stay locked until the attempt
  ends. A failed connect keeps the previous model, so file names cannot
  take the new model. A connected instrument cannot be re-connected as the
  other model. The list shows the model actually connected.
- **Identity:** both models must be named in the model field, "MODEL 2410"
  or "MODEL 2400", optionally with -C. A serial number containing 2410 no
  longer passes as a 2410, and a 2400-LV is refused.
- **Names:** the scan parameter log is named by the model that ran the
  scan. The page's image buttons, the image-folder dialog and the MOKE
  magnet warnings name the model or "MOKE-24" rather than "2410".
- **Second review:**
  - *Older gap:* a resistance start sends `*RST`, which sets the OVP to
    none. If that start then failed, the next manual voltage in the same
    range turned the output on without setting the OVP again. Now `*RST`,
    and the start of every voltage setup, clear what the controller
    trusts. The next voltage output therefore sets and checks the OVP
    first.
  - The OVP readback must also not be lower than asked for (within 1 %),
    since a lower level would silently clamp a scan.
  - The parameter log takes the OVP readback from when the scan turned
    the output on.
  - While a connect runs, the page offers nothing that sources (Start,
    manual, resistance, Disconnect).
  - Camera Live recordings from this page are named by the model.
- **To check on the real 2400 before use above 20 V:** after connecting,
  `:SOUR:VOLT:PROT?` should read 160.
- **Tests:** in `Other-files/test/test_instrument_safety_logic.py`, the fake
  2410 and 2400 select OVP levels as in the manual. New tests cover:
  - the 2400 at 160 V and 2410 at 200 V limits;
  - a missing OVP level refused;
  - the scan limit per model;
  - a failed connect and a re-connect as the other model;
  - the stricter identity check.

  `Other-files/test_panel_defaults.py` checks the hazard text and the lock
  during a connect. Each new check fails when its fix is undone.

## MOKE-24: the |R| plot draws, and Start returns after Stop (2026-10-07)

On the lab PC the continuous-resistance plot showed only its time axis and
"|R| (MΩ)": no |R| numbers, no trace and no "Current |R|" value. After
**Stop measurement**, the Start button stayed greyed out as "Measuring
continuously…".

- **Cause:** the program imports numpy together with the camera libraries
  (pco, Pillow). Where one of those is missing, `np` is None. The |R| plot
  used numpy to scale its values, so it stopped with an error just after the
  time axis. The same error, raised when the measurement finished, also
  skipped the step that re-enables the buttons.
- **Fix:** the plot scales its values in plain Python. The finish step now
  re-enables the buttons even if the plot fails to draw. The measurement
  itself was unaffected: the source was zeroed and turned off as before.
- **Tests:** `Other-files/test_keithley_resistance_plot.py` draws a steady
  369.711 MΩ, a changing value and a single point with `np` set to None.
  It also checks that the buttons are refreshed after Stop when the plot
  fails. Before the fix, these tests fail.

## MOKE-24: the MOKE-2410 page now drives a 2410 or a 2400 (2026-10-07)

The MOKE-2410 tab is renamed **MOKE-24**. Its connection row has a
**Model** list, 2410 (the default, as before) or 2400. The list is fixed
while the instrument is connected.

- **Identity check:** the model must match the instrument's `*IDN?`. The
  2410 check is unchanged. A 2400 must be named in the model field ("MODEL
  2400", also 2400-C), so a 2410, or a serial number containing 2400, is
  never taken for one.
- **Above 20 V:** the source range and the current allowed on it follow the
  model. The 2410 uses its 1000 V range with at most 21 mA, as before. The
  2400 uses its 200 V range with at most 105 mA (`KEITHLEY_MODELS`). Both
  keep the page's ±200 V software limit and 200 V hardware OVP, and the
  20 V range at or below 20 V. *(Superseded for the 2400 by the next entry
  up: a 2400 has no 200 V OVP level, so it is limited to ±160 V.)*
- **Labels and files:** the red off button names the model. Dialogs are
  titled MOKE-24. Saved scan files, images and parameter logs are named
  `Keithley<model>_…`, so a 2410's files keep their names. The parameter
  log records `instrument.model`. The Gate-VI page's own 2410 is unchanged.
- **Tests:** in `Other-files/test/test_instrument_safety_logic.py`, a fake
  2400 refuses the 1000 V range as the instrument does. The tests check:
  - a 2400 scan to 200 V on the 200 V range;
  - the 105 mA limit;
  - a 2400 driven as a 2410 failing before the output turns on;
  - the identity check, including connecting with each model;
  - the model fixed while connected.

  The page itself is checked in `Other-files/test_panel_defaults.py`.

## RT-E: a completed Current scan ends at zero current (2026-10-07)

When an RT-E **Current** scan completes, the 6221 is turned off: `SOUR:WAVE:ABOR`,
`CURR 0` and `OUTP OFF`, confirmed by reading `OUTP?` back. The status
reads "Completed; 6221 current set to 0 (output OFF, read back)". If the
readback is not OFF, the scan reports a failure.

It turns off only after the last point of the last harmonic pass, never
between passes. **Stop** keeps holding the outputs, and a completed **Field**
scan still holds both the 6221 and the KEPCO, as before. Tests in
`Other-files/test_rte_panel.py`.

## RT-E start-up settings: Zurich 1+2+3 at 17.3 Hz (2026-10-07)

The RT-E page now opens with the settings of the user's figure:

- 6221 0.1 mA RMS at 17.3 Hz (was 1 mA at 317.3 Hz), compliance 50 V;
- the Zurich measuring harmonics 1+2+3 together, ticked;
- SR830-1, -2 and -3 not ticked (SR830-1 was ticked before); their
  harmonics stay 1+2+3, 2 and 3;
- KEPCO 0 A with a 5 V limit; Current scan 0.01-1 mA in 0.01 mA steps,
  1 loop, settle 0.5 s, acquire 1 s, no round trip (unchanged).

The PPMS page is unchanged (1 mA RMS, 317.3 Hz, all meters ticked). The
check is `test_rte_defaults_are_the_users_figure` in
`Other-files/test_panel_defaults.py`.

## PPMS and RT-E: Zurich controls no longer stay greyed (2026-10-07)

On the RT-E page (and the PPMS page) the Zurich row could stay greyed while
the Zurich was connected. That covered Connect, Disconnect, the harmonic
list, Measure, Ref. and TC. The 1 s background read marked the Zurich
"busy", which is the flag that locks those controls. A control refresh
during a read, for example after KEPCO Zero / OFF, greyed them, and nothing
re-enabled them when the read ended.

- **Fix:** the background read now has its own flag and never locks the
  controls. "Busy" covers only Connect, applying a change, and Disconnect.
  A change clicked during a read queues behind it on the single Zurich
  worker. Before, a Connect or Apply clicked during a read did nothing.
- **Workaround on an older copy:** untick and re-tick the page's safety
  box. This refreshes the controls, usually between reads.
- **Test:** `test_a_background_read_never_locks_the_zurich_controls` in
  `Other-files/test_ppms_two_panels.py`. It fails on the old code.

## MS-fourState.py: PPMS state writing and zero-field read (2026-10-06)

A new program in the repository root. For each state of a sequence,
`MS-fourState.py`:

1. turns the PPMS rotator to the state's angle at zero field;
2. sets the write field (Driven);
3. sends the 6221 pulse (default ±22 mA, 30 µs, one pulse);
4. returns the field to 0 Oe;
5. reads the SR830 first harmonic X, Y and θ with a 0.5 mA RMS sine.

It plots X, the field and the pulse current per step, and writes a CSV. The
states follow the measurement table (D 0°/+0.15 T, A and B 270°/∓0.15 T,
E 0°/−0.15 T, with A' and B' the reversed field and current), all editable.
The default sequence is D A B E.

Its Mode list has two modes, at the user's choice (2026-10-07):

- **Cycle** (default) repeats a cycle of states (default D-A-B-E-B-A-D-…)
  for a set number of cycles, or until Stop.
- **Sequence** runs the states once, in the order typed; for example
  `A B A B A B` switches between two states.

On 2026-10-06 there were also two launcher files, `MS-cycleState.py` and
`MS-twoState.py`, and a "Two states" mode. All are now in the one file.

- **Instrument code:** it is the main program's. That covers the PPMS
  connection, field setpoints and their settled check, the rotator's
  verified Move, the 6221 controller, and the PPMS page's sine with its
  phase marker as the SR830 reference. It also covers the SR830 query and
  checked-write helpers and its connection baseline. The main program
  itself is unchanged.
- **Stops:** Stop, an error or the end turns the 6221 off and returns the
  field to 0 Oe, both confirmed by readback. Shutdown follows the field the
  run commanded, not a possibly stale reading. The rotator has no software
  stop.
- **Pulse:** the MOKE-6221 page's commands and order (ARM, `OUTP ON`,
  INIT), with every setting read back before ARM. The 6221's latched
  compliance bit is recorded for every pulse. What the 6221 outputs between
  ARM and INIT is not measured, so check it with an oscilloscope before
  the first run.
- **Review:** an independent review before the first hardware run found
  14 problems, and a second review of the fixes five more. All are fixed,
  each with a test, except the pulse shape between ARM and INIT, which
  needs the oscilloscope. Among them: a stale reading could skip the return
  to zero; a disconnect left an SR830 error latched, which failed the next
  Start; and one display error could freeze the window.
- **Docs:** `Other-files/MS-fourState_README.md` has the procedure, the
  checks before the first run (an oscilloscope look at the pulse; compliance
  15 V) and the data files.
- **Tests:** `Other-files/test_ms_four_state.py` runs against simulated
  instruments; breaking each safety rule in a copy is caught. The windows
  are checked on GitHub with `Other-files/ci/ms_four_state_checks.py`.
- **Status:** not yet run on the instruments.

## PPMS connection timeout: the network, not the DLL (2026-10-06, message fix open)

What happened: on the PPMS1 ThinkPad, Connect PPMS (QDInstrument remote,
`192.168.137.5:11000`) failed after about a minute with a dialog that begins
"Loading QDInstrument.dll or creating the remote PPMS session failed. Please
confirm the DLL is not blocked by Windows, that .NET Framework 3.5 is
enabled…", followed by a long .NET stack trace.

Cause: the network. The DLL had loaded. Inside the trace is
`EndpointNotFoundException` and TCP error 10060: nothing at
`192.168.137.5:11000` answered, three times over 21 s.

- The ThinkPad shares its Wi-Fi (NUS) with the PPMS computer through
  Windows Internet Connection Sharing. Its `Ethernet 2` adapter, cabled to the
  PPMS computer, is `192.168.137.1`.
- The PPMS computer's `ipconfig` showed a self-assigned address,
  `169.254.121.239`, instead of `192.168.137.x`. `ipconfig /renew` timed out
  ("unable to contact your DHCP server"): the ThinkPad's sharing had stopped
  handing out addresses, although the cable link was up on both sides.

Fix (connected again the same day):

1. On the PPMS computer, give the Local Area Connection a fixed IPv4 address:
   `192.168.137.5`, mask `255.255.255.0`, gateway and DNS `192.168.137.1`.
   This is the program's default PPMS IP, so it keeps working after reboots.
   The alternative is to restart the sharing on the ThinkPad
   (`Restart-Service SharedAccess` as administrator, or untick and re-tick
   Sharing on the Wi-Fi adapter), then `ipconfig /renew` on the PPMS computer.
2. Check from the ThinkPad before pressing Connect PPMS:
   `Test-NetConnection 192.168.137.5 -Port 11000` must report
   `TcpTestSucceeded : True`. If ping works but the TCP test fails, the
   QDInstrument server is not running on the PPMS computer
   (`netstat -an | findstr 11000` there shows no `LISTENING` line).

Still open in the program: `PPMSQDInstrumentReadbackController.connect` in
`Transport_all_NH_dev.py` wraps every failure in the DLL/.NET message, so a
network fault reads like a DLL fault, and the dialog shows the whole stack
trace. Proposed fix: a 3 s TCP check of host and port before the DLL is
loaded, with a short message that names the address and says whether it
timed out (wrong IP, link or firewall) or was refused (server not running),
and the full trace in the log only. Not started.

Also seen: the lab PC was running a copy older than 2026-09-30 (its Zurich
MFLI field was plain text "MF-DEV4414" instead of the "DEV4414 (Super)" list),
so it lacked the later changes, including Set H/Zero H ending Persistent.
Copy the current folder before measuring.

## PPMS: Set H and Zero H end Persistent (2026-10-04)

- On the PPMS page, Set H and Zero H now send the Persistent end mode. The
  PPMS ramps to the field, closes the persistent switch and reports
  StablePersistent, and the field status under Live readings shows each
  step. They used to end Driven, because the page's hidden end-mode list
  defaulted to Driven. Set H can be used in every scan mode that does not
  scan the field; Zero H in every mode (the user's choice).
- The status line beside Connect PPMS follows the command. It shows
  "Going to X Oe, then Persistent…", then "Field X Oe reached; Persistent
  confirmed" once a reading begun after the command shows Persistent within
  2 Oe of the setpoint. Readings begun before the command never count. If
  the PPMS goes Persistent at another field, or is not Persistent within the
  ramp time plus 30 min, the line says so and asks you to check MultiVu.
  When something else commands the field before that (a field scan's
  first field command, a Macro FIELD line, "Zero field + persist after
  scan", or the scan clean-up zeroing after "I off" or an error), the
  follow-up ends and the line says which. A scan or Macro that never
  commands the field leaves it running. These are messages only: nothing
  is resent. The follow-up uses the QDInstrument status names (the PPMS1
  interface); with MultiPyVu the line says "Sent X Oe, ending Persistent;
  see the field status" instead.
- Field scans that start at a field the magnet holds Persistent now wait
  for the Driven status after their first command, up to the block's
  existing 3600 s limit. Before, a Persistent status read just after the
  command could pass as settled, and the switch warming afterwards stopped
  the scan and zeroed the field. Nothing new is sent.
- Unchanged: field scans hold every point Driven; the stop and close-time
  zeroing, "Zero field + persist after scan", the Persist button and the
  Macro send what they did before.
- Tests: `Other-files/test_ppms_field_persistent.py` (stubs and a fake PPMS
  with a fake clock, no Tk) and a check of the page default in
  `Other-files/test_panel_defaults.py`.
- Not yet tried on the PPMS. For the first use, set a small field near the
  present one and watch MultiVu warm and cool the switch.

## Test_Microwave_NH_dev.py: SR830 setup as in the main program; old copies removed (2026-10-01)

- The SR830 setup now sends `ILIN 0` (both line notch filters off) and then
  `SYNC 0` (synchronous filter off), as the main program does. It used to
  send `ILIN 1`. This is the only change to instrument commands.
- Removed: the older English copy `Calibrations/Microwave_Transport_Test_NH_dev.py`
  (17 September), and the files in `Other-files/microwave_test_support/`
  that once generated the program (`app_body.py.txt`, `controller_snapshot.py.txt`,
  `build_standalone.py`, `extract_controllers.py`, `controller_provenance.json`).
  The program is now edited directly. Git history keeps the removed files.
  The grid-scan tests in that folder stay and run in the cloud check.
- The program's README is now `Other-files/Test_Microwave_NH_dev_README.md`.

## Test_Microwave_NH_dev.py: English, both Zurich lock-ins, tidier window (2026-10-01)

The standalone fixed-current microwave x modulation grid test is in the
repository root as `Test_Microwave_NH_dev.py`. Its first commit is the file
exactly as received.

- All text is English: comments, messages, logs, dialogs and the window.
  The controller code changed only in text. A comparison of the program's
  syntax tree with every string blanked shows no other change, and the
  wording follows the main program wherever the code is shared. The lock-in
  lamps now read "Locked", "Not locked", "Range OK" and "Overload", and
  durations read like "1 h 4 min 37 s".
- The MFLI field is the same list as on the main program's pages:
  "DEV4435 (Normal)" and "DEV4414 (Super)". Each entry tries only the Data
  Servers that can reach it, so DEV4414 uses 192.168.68.250 and DEV4435
  keeps its previous search. A failed connection names the other lock-in,
  and the run record names the MFLI device and identity.
- Window: the five connections form one aligned grid in two groups. The
  parameters form an aligned table with each label beside its field. The
  lamp texts are wide enough for their English, and the status wraps to the
  window width, so a saved-folder path is never cut. On Windows at 96 DPI
  nothing is clipped or cut at the program's 1360 px window or at 2000 px.
- Tests run on GitHub's Windows and Linux machines:
  `Other-files/test_microwave_test.py`, the layout check
  `Other-files/ci/microwave_checks.py` with screenshots, and the 25 existing
  grid-scan tests in `Other-files/microwave_test_support/`. Those tests now
  load this file; before, they imported a module that no longer existed, so
  they never ran.
- Not changed, and worth deciding: this program's SR830 setup sends
  `ILIN 1`, while the main program sends `ILIN 0` and then `SYNC 0`. There
  is also an older English copy, `Calibrations/Microwave_Transport_Test_NH_dev.py`
  (17 September), and the build sources in `Other-files/microwave_test_support/`
  (`app_body.py.txt`, `controller_snapshot.py.txt`) still hold the old text.

## Other pages tidied for the lab PC (2026-10-01)

MOKE light, MOKE motor, MOKE magnet, MOKE-2410, Gate-VI, PPMS and RT-E were
reviewed from the Windows screenshots at the lab settings, and their layouts
fixed. Commands, texts, variables and instrument behaviour are unchanged:
the program's string constants, command bindings and attribute assignments
were compared before and after.

- At the lab's 2000 px window, every page now has nothing clipped and no
  text cut on Windows. Before:
  - MOKE magnet cut nine button labels, among them Apply exposure,
    Histogram, Recapture BG, Start recording and Zero current.
  - MOKE-2410 cut "Start recording".
  - Gate-VI cut the end of its interlock sentence.
  - PPMS showed "Conne" and "Discor" on its instrument buttons.
  - RT-E hid most of the Zurich "TC (s)" box.
- At the 1101 px window the program opens with, MOKE light, MOKE motor,
  MOKE magnet, MOKE-2410, Gate-VI and RT-E now fit too. Before, buttons such
  as Stop now and Query all ran past the edge. PPMS still clips some
  controls at 1101 px; widen the window for that page.
- Columns are aligned, readouts have fixed widths so a new reading does not
  shift its neighbours, and long safety texts wrap word for word. Long
  status messages (the MOKE stage, the PPMS Zurich line, RT-E) are cut to
  fit with an ellipsis ("…"); the full text is still in the log and in the
  dialogs.
- The cloud check now also draws the MOKE motor page, which it used to
  leave blank, and no longer reports wrapped text as cut. All tests pass on
  GitHub's Windows and Linux machines (355 + 104).

## Windows layout fixes, checked on GitHub's machines (2026-10-01)

Every page is now photographed on GitHub's Windows and Linux machines at the
lab PC's settings: a 2560x1440 screen at 96 DPI, with the 2000 px widened
window and the 1101 px window the program opens with. Windows uses the same
fonts and theme as the lab PC. These runs found several problems that the
Mac's narrower fonts had hidden. Fixed:

- FMR: the gaussmeter status sits beside its controls instead of alone at
  the far right. The field readout has a fixed width, so a reading no longer
  shifts Read once and the checkbox. The KEPCO Mode, I, V and OUTP readouts
  each fill the control group above them, here and on STFMR.
- STFMR, 1101 px window: the Loop box moves to the end of the SR830 row. As
  a sixth field on the current row it was pushed off the page on Windows.
  The KEPCO safety sentence now wraps onto two lines, word for word.
- MOKE-6221, 2000 px window: the scan and ROI-editing boxes now sit side by
  side on Windows, so the plots are about 170 px taller. The ROI buttons
  have their own row, and the ROI mean has a fixed width. The page's layout
  no longer depends on how the window was resized.
- RT-E: a long status message is cut to fit with an ellipsis ("…"). The
  full text is still in the error dialog and the log.
- Tests: they now build the program at the lab settings
  (`Other-files/lab_screen.py`) and use the lab's 2000 and 1101 px widths.
  Before, they used the computer they ran on, so they only held on the Mac.
  The cloud check fails if the lab screen shows clipping or cut text on
  MOKE-6221, STFMR or FMR, or if STFMR or FMR stop sitting side by side at
  2000 px. Both machines pass. The 1920x1080 screen's 826 px window still
  clips the KEPCO buttons; that is reported only, since the lab screen is
  2560 px.

## Zurich list entry renamed (2026-09-30)

- "DEV4435 (USB)" is now "DEV4435 (Normal)", at the user's request, to pair
  with "DEV4414 (Super)". Only the label changes. It still connects DEV4435
  over its USB link, and the failure messages use the new name.
- Checked without opening windows: the program compiles, the four `test/`
  suites pass, and the name fits the narrowest list (STFMR, about 98 of the
  112 px available at the lab setting, from font metrics). The GUI suites
  were not rerun on the Mac, because they take over its screen.

## Both Zurich lock-ins selectable on every page (2026-09-30)

- **Bug:** STFMR could not connect the Super Zurich. Its MFLI connect only
  tried DEV4435's Data Servers, a PC-side LabOne server at 127.0.0.1 and
  DEV4435's own server over USB at 192.168.69.78. DEV4414, the Super Zurich, is
  on the lab network at 192.168.68.250, where MF Device Finder lists it, and
  each MFLI's server sees only that MFLI. PPMS had the same bug (see the
  2026-09-18 entry). Only RT-E had the right address.
- The Zurich field on STFMR, Gate-VI, PPMS and RT-E is now a list with two
  entries: "DEV4435 (USB)" and "DEV4414 (Super)". Each entry tries only the
  servers that can reach it. DEV4414 uses 192.168.68.250:8004. DEV4435 keeps
  its previous search.
- Each page keeps its previous lock-in by default: DEV4435 on STFMR and
  Gate-VI, DEV4414 on PPMS and RT-E. The lists can be chosen from but not
  typed in. On STFMR, Gate-VI and RT-E they are as wide as the address fields
  around them, so the Connect buttons stay in line.
- A failed connection now names the other lock-in by its list entry. The
  DEV4414 message leaves out DEV4435's USB address, so it never sends anyone
  to the wrong cable.
- Records: the STFMR run log gains `mfli_device` and `mfli_identity`. The
  Gate-VI CSV `resource` column now holds the device actually connected
  rather than the text in the field.
- Known gap, next: PPMS and RT-E files do not yet record which Zurich took
  the data. That matters now that RT-E can choose DEV4435. As before, the
  Gate-VI list can still be changed while connected.
- New tests in `test_zurich_choice.py` cover the lists, the servers each page
  tries, and the messages. They fail on the previous commit. Checked on the
  Mac against the emulated lab screen; not yet on the lab PC.

## STFMR status lamps aligned (2026-09-30)

- The live readouts and the Ref and MFLI Overload lamps under the STFMR sweep
  buttons now share one grid. The field, Ref and Overload texts start at one
  x, and the voltage, Ref and Overload boxes end at one x. Before, the texts
  started at 0, 18 and 22 px and the boxes ended at 464, 414 and 126 px. The
  two lamps sit in one column, each level with the first line of its text,
  including the two-line MFLI + SR830 readout.
- The column is 20 px wider, for the lamp gutter. It still sits beside the
  parameter table at the lab's 2000 px window and stacks as before at
  1101, 826 and 1586 px, with nothing clipped.

## Compact MOKE-6221 page, and the STFMR field at 0 A (2026-09-30)

- The MOKE-6221 page is compacted like STFMR. The 6221 connection sits
  beside the continuous-output box, and the pulse scan beside ROI editing.
  The scan's start, train-start and Save CSV buttons move to their own row
  under the scan parameters, whose labels and boxes are now aligned. At the
  lab's 2000 px window the controls need 245 px less height. The plots take
  the spare height down to the status line; before, an empty row below them
  took it.
- Each side-by-side pair, and the Apply and red stop buttons of both
  outputs, stacks by itself when the window is too narrow. Checked on a
  2560x1440 screen at 2000 px and at its default 1101 px window, on a
  1920x1080 screen's 826 px window, and at 1586 px: nothing is clipped and
  no text is cut.
- The STFMR "Measured field vs 4886 current" plot now states the field at
  0 A, for example "Field at 0 A: +272.0 G". The value is the measured field
  where a sweep crosses 0 A, interpolated between the points either side.
  Frequencies and loops that agree within 2 G give one value. Otherwise the
  label shows the range, because that spread is remanence. The label sits
  outside the plot box, so it never covers the curve. If the legend leaves
  no room, it moves into the plot's title bar instead.
- Tab order follows the screen: on STFMR and FMR the connections come
  before the 4886 output, and on MOKE-6221 the scan parameters come before
  the start buttons.
- A review of this batch confirmed two problems, both fixed and covered by
  tests: that Tab order, and the 0 A label overlapping the axis title on a
  narrow plot. The `test/` suite found a third: given a stand-in canvas,
  as the stub tests use, the label raised an error that stopped the plot
  redraw. It now draws nothing there.
- The suites hang on macOS when the display is asleep, because Tk's
  `update()` waits for the window server. The previous commit hangs the
  same way. Wake the display, or run under `caffeinate -u`. The `test/`
  command in the top-level README needed `PYTHONPATH=../..`, not `..`.

## STFMR and FMR layout, and the STFMR sweep safety tick (2026-09-30)

- On both pages the instrument connections now sit left of the 4886 output
  panel, which takes the spare width.
- STFMR field-sweep parameters are one aligned table: each label sits against
  its field and every field in a column has the same width, so the boxes line
  up down the page. The lock-ins take two rows, SR830 then MFLI, which leaves
  room for the sweep buttons, the LS475 readback, the live values and the
  MFLI overload lamp in a column on the right of the table. The save path
  keeps a full-width row below. "MFLI time constant s" is shortened to
  "MFLI TC s".
- **The STFMR sweep's own safety line is removed, as requested, and a sweep
  now requires the tick in the 4886 output panel instead.** That line was the
  interlock the sweep checks before energising the magnet and the RF, so
  deleting it alone would have made sweeps impossible to start. The panel's
  wording gained "microwave power", so it still covers everything the removed
  line did, and the run log's `safety_confirmed` records that tick.
- The FMR scan is split into two columns, the field sweep and the fixed-field
  frequency sweep, each with its own buttons and progress bar. FMR's plots
  now take the spare height down to the status line, instead of an empty row
  below it taking it.
- Truncated FMR text is fixed: Connect and Disconnect, "PNA power dBm",
  "Fixed-field frequency sweep" and "Save sweep CSV + PNG" all show in full.
- Every side-by-side section stacks automatically when the window is too
  narrow. Checked on a 2560x1440 screen at 2000 px and at its default 1101 px
  window, on a 1920x1080 screen's 826 px window, and at 1586 px: nothing is
  clipped and no text is cut.
- An adversarial review of this batch found no problem with the re-gated
  interlock and confirmed four layout defects, all fixed:
  - The fixed widths given to the live readouts cut the two-line MFLI + SR830
    readout and SR830 autorange messages. They are now sized to the longest
    text each can hold, so the column never changes width as readings arrive.
  - In a narrow window every field shrank equally, cutting "MFLI + SR830".
    Now only the plain number fields give way, down to a six-character floor.
  - The side-by-side decision ignored the parameter box's padding. It also
    failed silently, because ttk returns padding as a tuple; that parsing bug
    was found while re-measuring and is fixed too.

## STFMR manual field output, compact STFMR and FMR layouts (2026-09-29)

- The STFMR page gains the FMR page's "KEPCO 4886 BOP 20-10 field output"
  panel: safety confirmation, Current or Voltage mode, target and limit,
  Apply output, Show sent values and the red Zero / OFF. Apply and Show work
  only when the KEPCO is connected and no sweep is running, since a running
  sweep owns the supply. Zero / OFF stays live whenever the KEPCO is connected,
  and pressed during a sweep it stops the sweep and zeroes at once. The status
  line follows each point the sweep sends.
- On both pages the 4886 output now sits left of the instrument connections,
  which form one compact column. The two sections share one row, so the STFMR
  plots are about 46 % taller. When the window is too narrow for both, for
  example the 826 px split-screen window the program opens on a 1920 px
  display, they stack automatically instead of clipping.
- Address fields no longer stretch across the row: they keep an 18-character
  width, which fits every GPIB default, and give way first if a row is too
  narrow. "Not connected" is no longer cut to "Not conn".
- The STFMR field-sweep parameters follow the PPMS pattern: fixed-width
  fields packed from the left, one row each for the microwave carrier, the
  magnet sweep and the lock-ins. That is four rows instead of five. The
  lock-in row wraps onto two when the window is narrow.
- An adversarial review of this work confirmed six defects before it reached
  the magnet; all are fixed:
  - **Zero / OFF could silently do nothing.** It only undid an output this
    page had switched on, so an output left on by the front panel, LabVIEW,
    another page or a crashed session was not touched, while the panel said
    "zeroed and output off". The red button on both pages now always sends
    the zero setpoint and `OUTP OFF`. With the mode unknown it zeroes both
    setpoints, which only drives the output down.
  - **Zero / OFF could freeze the window** for up to 15 s while the sweep
    worker held the supply during start-up with a stalled card. It now never
    waits on the Tk thread: the sweep is told to stop, which zeroes the
    supply as soon as the transfer returns.
  - **The display could claim "OUTP: OFF" falsely.** Before this session has
    sent the 4886 anything, its state is shown as unknown, and an output left
    possibly on by a failed zero/OFF reads "NOT CONFIRMED OFF", on FMR too.
  - The split-screen overflow described above.
  - Two weaknesses in the tests: a stray `unittest.main()` that skipped seven
    tests when the file was run directly, and a simulated card that did not
    record the writes it stalled on.
- Each safety test was checked by putting its defect back and confirming the
  test fails. New and updated tests: `test_stfmr_field_output.py` and
  `test_stfmr_visa_timeout.py`.

## KEPCO 4886: device clear removed, stalled card needs a power cycle (2026-09-29)

- A root-cause review of the NI traces, every 4886 code path and all 64 run
  logs overturned the premise of the interim fix below. The SR830-mode
  correlation is tied to the date: every attempt on 2026-09-29 was SR830 mode,
  and the same order, SR830 lock then KEPCO enable, completed five times on
  2026-09-07. GPIB addressing also rules out a mechanism, since the 4886 is not
  a listener while the SR830 is addressed.
- The surviving explanation is that the BIT 4886 card itself stopped
  completing the data handshake. `VI_ERROR_TMO` rather than `VI_ERROR_NLISTENERS`
  means something at address 6 takes its listen address and then never accepts
  the byte. The LabVIEW traces show all 132 writes to this card finishing in
  2-6 ms, so it is stalled, not slow. The project GPIB scanner already records
  that this card stops responding after repeated `*IDN?`.
- **Operator action: power-cycle the BOP 20-10, which resets its BIT 4886.**
  Close the program, NI MAX and LabVIEW first. Do not run NI MAX "Scan for
  Instruments", `Visa_list.py` or `visa_device_check.py` afterwards, since they
  send `*IDN?` or a clear to address 6.
- The interim device clear and full resend are removed. They had never run on
  hardware, went against the project's write-only handling of this card, and
  had a hazard: after an aborted transfer, a garbled resent `FUNC:MODE` could
  leave the supply in voltage mode, so `CURR 0;VOLT 10` and `OUTP ON` would put
  10 V across the magnet. The enable sequence is sent exactly once again.
- The KEPCO timeout is back to 3 s, the value in the LabVIEW traces. 10 s
  changed nothing except making every hang, and the zero/OFF GUI freeze,
  longer.
- Two existing safety gaps in the same path are fixed:
  - The output is now claimed before `OUTP ON` is written, not after. A card
    can act on `OUTP ON` and then stall its handshake, and previously that left
    the supply possibly on with no zero/OFF attempted.
  - Zero/OFF sends `OUTP OFF` even if the zero setpoint write is refused, and
    keeps ownership when either write fails, so nothing reports the output as
    confirmed off.
- Kept from the earlier fix: every start-up step and per-point read names its
  instrument, and a timed-out LS475 or SR830 read is retried twice. Reads
  cannot change the supply, so retrying them is safe.

## KEPCO 4886 recovery after a stalled write (2026-09-29, interim, SUPERSEDED)

> Superseded the same evening by the entry above: the device clear and resend were removed.

- The instrument-naming change confirmed the KEPCO 4886 is the device that
  stops responding, while enabling the output at the first current. Raising
  its timeout to 10 s did not help, so the supply is refusing a write rather
  than being slow.
- Across all 64 STFMR run logs, today's three failures are the only runs with
  the MFLI disconnected and the only SR830-meter runs on this program, which
  had run MFLI-only until now. Python 3.14, the KEPCO address, the 10 V limit
  and the -3 A start had all completed before. So the enable sequence itself
  is proven, and it fails right after heavy GPIB traffic to the SR830, which
  the MFLI path never produces.
- When a write to the 4886 times out, the program now sends a GPIB selected
  device clear, waits 0.3 s and resends the whole output sequence once. It
  restarts at `FUNC:MODE`, never at the failed command: the clear may return
  the card to its power-on state, and resuming could send a current setpoint
  to a supply that has fallen back to voltage mode.
- If the resend also stalls, the error names the exact command and asks for
  a power cycle of the BOP and its BIT card. Any output that was switched on
  is switched off again first.
- This is an interim fix. A root-cause analysis of the NI traces and the
  other 4886 code paths is still running.

## STFMR scans failing with a bare VI_ERROR_TMO (2026-09-29)

- Three STFMR sweeps failed identically, 6.35 s after `frequency_configure`
  and before a single point was saved. The logs show the run never reached
  `frequency_ready` or the start pre-read, so the fault is in the block that
  sets up the SMB100A, the SR830 and the KEPCO, not in the measurement loop.
  The repeatability to within 0.03 s rules out a chance bus glitch.
- The dialog said only `VI_ERROR_TMO`, naming none of the four instruments on
  the bus. Every instrument call in that block, and every per-point read, now
  reports which device stopped responding and what it was doing.
- The KEPCO 4886 transfer timeout goes from 3 s to 10 s. It is the shortest
  on the rack, and the start-up block ends by sending the auto-range commands
  and `OUTP ON`, which make this old supply hold the GPIB handshake while its
  relays switch. That is the best candidate for a failure that lands at the
  same moment every time.
- A timed-out read is now retried twice before the scan is abandoned, matching
  what the PPMS rack already did. Writes are deliberately not retried, since
  re-sending an output command could apply it twice.
- Checked and ruled out as causes: the acquisition executor has one worker,
  the 0.5 s continuous field read already pauses during a scan, and the manual
  LS475 button is already disabled while scanning.
- Still open: the SR830 reference reads 317.3 Hz, matching the modulation, yet
  reports WARNING, which means its latched unlock bit is set. That points at a
  genuinely intermittent reference from the SMB100A low-frequency output and
  is worth checking at the cable.
- `test_stfmr_visa_timeout.py` covers timeout detection by code and by text,
  the naming, the retry and its limits, and the KEPCO timeout value.

## Project folder renamed to 0-Transport-Code (2026-09-22)

- The project moved from `3-Transport-Code` to `0-Transport-Code`. The
  program has no hard-coded paths to its own folder, so nothing in it needed
  changing, and all tests and the headless build pass from the new location.
- The two README instructions that named the old folder are corrected. The
  Python setup instructions further down were already broken before this
  move, since the environment still points at the original `0-Codex/3-Transport`
  folder; they now give the invocation that works.
- The folder is now a git repository on branch `main`, starting from a
  snapshot of this state.

## 6221 output low forced to floating (2026-09-21)

- Both 6221 bring-up paths now put the instrument in the front panel state
  TRIAX -> OUTPUT LOW -> FLOATING, using `OUTP:LTE OFF`. Earth-grounding the
  output low ties the sample to mains ground and forms a loop with the PPMS
  rack and the lock-in inputs.
- The state is read before it is written, so an instrument that already
  floats is not written to at all. This matters on the PPMS and RT-E rack,
  which does not reset the 6221 on connect and can find it already sourcing.
  The MOKE-6221 page resets first, so its output is off either way.
- The result is read back. If the instrument does not take the setting, the
  connection fails with a message naming the front panel path rather than
  running a measurement on a grounded output.
- `test_k6221_output_low.py` covers both paths: grounded gets set, floating
  is left alone, a refusal raises, and the reply parser accepts 0/1 and
  ON/OFF.

## 2410 tab renamed to MOKE-2410 (2026-09-21)

- The Keithley 2410 page is on the MOKE rack, so its tab now reads
  "MOKE-2410", matching the earlier MOKE-6221 rename. Only the tab title
  changed. The instrument key, the CSV columns and the saved filename
  prefix are untouched.
- `test_panel_defaults.py` now pins the full tab strip, so a page cannot be
  renamed, added or reordered unnoticed.

## RT-E status on one line (2026-09-21)

- The RT-E scan status wrapped onto two lines. The label was one grid cell
  beside the progress bar, about 270 px, while the messages run to roughly a
  hundred characters. It now spans the panel and shares the row with the
  progress bar, so it gets every pixel the bar does not use, 434 px at the
  reference window size. The panel keeps its original height, so the plots
  lose none.
- The 6221 readback beside the settings no longer wraps at 230 px either. It
  was wrapping at high display scaling even though the text is short.
- Two 6221 fault messages said "during the PPMS field scan" although they
  also fire on RT-E, and the compliance one was long enough to wrap on its
  own. They now read "6221 hit compliance; scan stopped and output turned
  off" and "6221 output turned off unexpectedly during the scan".
- RT-E failures are passed through `short_status_error` as the PPMS page
  already did, so no single message can overrun the status line.
- Fixed a shutdown gap found while testing: the 6221 output monitor
  reschedules itself every 300 ms, and closing stopped it from booking the
  next call but left the booked one to fire after the window was gone,
  raising a Tk error. `safe_close_k6221` now cancels it.

## RT-E start-up defaults (2026-09-21)

- RT-E now opens with SR830-1 set to 1+2+3 and the only meter ticked for
  Measure. SR830-2 stays on the 2nd harmonic and SR830-3 on the 3rd, both
  unticked, so ticking either one needs no further setup. The Zurich stays
  on 1+2+3, unticked.
- A scan therefore runs three passes by default, one per harmonic, and the
  plot area opens with three rows.
- The PPMS page is unchanged: SR830-1/2/3 read the 1st/2nd/3rd harmonic and
  every meter is measured.
- These defaults live in the RT-E constructor, which the stub-based tests
  never run, so `test_panel_defaults.py` checks them against a fully built
  application.

## Faster scan plots, one harmonic implementation (2026-09-21)

- The transport plots are redrawn after every acquired point, and the cost
  grew with the number of points already on screen, so a long scan spent
  more and more time repainting. A redraw of nine harmonic plots is now
  about twice as fast: 51 ms to 26 ms at 600 points, 99 ms to 45 ms at
  1200. What is drawn is unchanged, verified by comparing all 795 draw
  calls the PPMS and RT-E pages make across every harmonic mode and all
  four x axes.
- Where the time went:
  - The page found the connected SR830 addresses by running a regular
    expression over every column of every row. It now tests three fixed
    column names and stops at the first row that has them.
  - X, Y and phase of one meter live in the same row, so each row was
    visited three times. The redraw now resolves a meter's columns once per
    harmonic order and reads all three channels in one visit.
  - The instrument address, the harmonic column name and the symmetric
    field range were recomputed inside the row loop or once per plot. They
    do not change during a redraw, so they are resolved once.
- A long scan puts far more points on an axis than the plot has pixels, and
  one canvas marker per point dominated the redraw. Markers of a series all
  share one shape, size and colour, so a marker landing on a pixel box
  another already covers paints exactly what is there and is now skipped.
  At 1200 points this drops 21879 canvas items to 12366. The rasterised
  image is identical; `test_plot_redraw_performance.py` pins that.
- RT-E had its own copies of the harmonic-mode parser, the per-pass scan
  schedule, the first-pass order and the measured-order map, which had
  drifted into duplicates of the PPMS versions. RT-E now delegates to the
  shared implementations, so the two pages cannot disagree about what
  "1+2+3" means. `_rte_harmonic_orders` and `_rte_harmonic_passes` are kept
  as names.
- Removed `_ppms_sr830_headroom_code`, left unused by the step-by-step
  autorange rewrite.

## PPMS lock-in status matches RT-E (2026-09-21)

- The PPMS harmonic rows no longer carry their own indicator dot and
  "Not read" text. Each row is now selector, Measure and autorange status,
  the same shape as RT-E; the reference indicator is still registered so
  existing status updates keep working, it is simply not drawn twice. Row
  labels are unchanged ("SR830-1 harm.").
- The PPMS instrument rack now reports the lock-in state for its SR830 cells
  (Not read / Locked / Warning / Not connected) instead of a plain
  connection string, as RT-E already did. The full diagnostic text is
  unchanged underneath.
- `_rte_compact_status` moved to the shared base class as
  `_ppms_compact_status` so both pages use one implementation, and it gained
  a "Not read" state so a meter that has never been polled is no longer
  shown as Connected.

## Zurich autorange on simultaneous harmonics (2026-09-21)

- The "Zurich harm." label now reads "Zurich harmonic".
- The Zurich autorange status shows the range in use, e.g. "Autorange 1 mV",
  matching the SR830 rows. One Signal Input 1 range serves every
  demodulator, so that figure applies to all selected harmonics at once.
- A scan point no longer reads the demodulators twice. It used to snapshot
  all four demodulators, run the ranged read, then snapshot them again
  unconditionally, so a 1+2+3 point paid for six demodulator reads instead
  of three. The snapshot is now retaken only when Signal Input 1 actually
  moved, which is the only case that invalidates it.
- The range decision itself is unchanged and stays correct for simultaneous
  harmonics: it uses the raw input extrema (ADC utilisation), not any single
  demodulator's R, because the harmonics share one input range.

## PPMS multi-harmonic scans; 6221 tab renamed; IV page removed (2026-09-21)

- Each PPMS SR830 slot now offers 1, 2, 3, 1+2 and 1+2+3, like RT-E. A
  multi-order selection runs the whole scan once per order (a "harmonic
  pass"), reconfiguring the source between passes, which rewrites HARM for
  the meters still active in that pass. A meter drops out once its orders
  are done. The estimated point count is multiplied by the number of passes,
  and the plot shows one row per measured order.
- CSV: a `harmonic_pass` column was added, and rows from later passes keep
  the first pass's columns (blank where a meter has finished) so the live
  CSV stays aligned. `sr830_<address>_harmonic` already recorded the order
  each meter measured.
- The harmonic variables became StringVars (they must hold "1+2"), and
  `_ppms_harmonic_orders` / `_ppms_harmonic_passes` moved to the shared base
  class, so the PPMS and RT-E pages use one implementation.
- The `6221` tab is now `MOKE-6221`.
- The `IV` page (Non-local IV) was removed: its tab, UI and 21 exclusive
  methods (about 1,600 lines). Helpers that only look IV-specific by name
  are kept because other pages use them: `_draw_nonlocal_series` (STFMR,
  PPMS, RT-E, Gate-VI) and the reference/MFLI-lock display chain
  (`_publish_reference_sync`, `_set_mfli_lock_state`,
  `_format_nonlocal_reference_status`), which Gate-VI calls with `vi=True`.
  Nine stale `_update_nonlocal_controls()` calls in the 2410, 6221 and
  Gate-VI pages were removed, and the shutdown list no longer has an IV
  entry. Offline suites green.

## SR830 autorange during scans, and per-meter range status (2026-09-21)

- Scan acquisition now autoranges in both directions. It previously only
  widened the range (`allow_down=False`), so an RT-E harmonic pass that
  followed a much larger order kept the coarse range found for that order:
  the 3rd-harmonic readings came back quantised in steps of the old range's
  resolution. A range change still restarts the acquisition window, so no
  saved point mixes two ranges.
- Changing an SR830 harmonic between RT-E passes clears that meter's
  down-count and cool-down, since the range held for the previous order says
  nothing about the new one.
- Each SR830 row now shows its autorange status right of the Measure tick
  (column 18), reading e.g. "Autorange 100 uV" for the full scale currently
  selected. The Zurich "Autorange ON" label moved to the same column on both
  the PPMS and RT-E pages, so every meter's autorange status lines up.
- Tests: both-direction ranging in the scan sample path, the full-scale
  status text, and the harmonic-change reset. Offline suites green.

## PPMS SR830 down-range rule (2026-09-19)

- A separate rule narrows the SR830 range by one code when the amplitude R
  has been below 10 % of full scale for three consecutive settled readings
  with the reference locked (`PPMS_SR830_DOWNRANGE_FRACTION`,
  `PPMS_SR830_DOWNRANGE_READINGS`). One code down lands the signal at
  20–25 % of the new range. It runs only in idle polling with the PPMS
  excitation on and between scan points, never inside a point's acquisition
  window (`allow_down=False` there). A reading above 10 % resets the count.
- After any range change in either direction, three readings are skipped
  (`PPMS_SR830_RANGE_COOLDOWN_READINGS`) so the two rules cannot oscillate.
  The overload step-up rule is unchanged.
- Tests: three-reading step down with cool-down, skipped when unlocked /
  excitation off / in a scan window, count reset. Offline suites green.

## PPMS scan: first point acquired until it settles (2026-09-19)

- The first point of every field sweep segment is now acquired in repeated
  windows of the normal acquisition time until two consecutive window
  averages agree, and the last window is kept. Agreement means every
  lock-in's X and Y moved by less than 2 % of its amplitude R, with a floor
  of 0.2 % of the SR830 full scale so a near-zero channel is not held by
  noise (`PPMS_FIRST_POINT_TOLERANCE_FRACTION`, `PPMS_FIRST_POINT_FLOOR_FRACTION`).
  At most 10 windows are used (`PPMS_FIRST_POINT_MAX_WINDOWS`); if the
  reading is still moving after that, the last window is kept and the log
  says so. A fixed one-window warm-up (2026-09-18) was not enough with a
  1 s time constant and the range steps that often happen at the start.
- Every other point is unchanged. CSV columns: `warmup_s` (time spent in
  discarded windows) and `first_point_windows` (windows used, 1 for all
  other points). The status line shows "First point: settling, window k/10…".
  The scan-time estimate does not include the extra windows.

## PPMS SR830 autorange: overload-only, one code per step (2026-09-18)

- The PPMS SR830 autorange is now the simplest possible rule: the range
  changes only when an overload is reported, and then widens by exactly one
  sensitivity code per settle (5 time constants). There is no 1 V probe, no
  direct jump, no proactive widening near full scale and no automatic
  narrowing on a small signal.
- Kept from before: the latched status byte is read once and discarded after
  each range change, and an overload bit is confirmed with a fresh read
  before acting ("OVERLOAD (transient)" when it has already cleared);
  overloaded readings stay out of the point average; an overload at the
  1 V range stops with the output-kept error.
- Any overload report now ranges up immediately: the fresh confirming read
  was dropped, and the idle poll steps an overloaded lock-in even when the
  PPMS page has not set the 6221 current itself (the scan worker still owns
  ranging during a scan). Only the post-change discard of the latched status
  byte remains, to avoid a double step from the transition.
- Tests updated to the new rule. Offline suites green.

## PPMS He history in one continuous CSV (2026-09-18)

- The helium-level history is now appended to a single file beside the
  program, `PPMS_He_history.csv`, instead of a new time-stamped file per
  session. The header is written once when the file is created (or found
  empty); later sessions append rows. Columns are unchanged:
  `timestamp_local` is absolute wall-clock time, `elapsed_s` restarts at
  each session's first sample.
- Write failures are still reported once per session and never stop the
  measurement. Test updated; offline suites green.

## PPMS SR830 reference on the rising edge (2026-09-18)

- The PPMS page now configures its SR830s with `RSLP 1` (TTL rising edge)
  instead of `RSLP 2`, via `PPMS_SR830_REFERENCE_SLOPE`, both at connection
  and in the pre-scan check. If a lock-in is found on another edge or
  reference mode at scan start (e.g. left over from the Non-local pages),
  the PPMS setting is rewritten and read back instead of aborting.
- The Non-local IV/VI and STFMR pages keep their own reference settings.
  Tests updated; offline suites green.

## PPMS SR830 autorange: fast range seek (2026-09-18)

- Symptom: SR830-2/3 stayed at "OVERLOAD / LIAS=7" or "LIAS=1". The SR830
  status byte is latched (every overload since the previous `LIAS?` read),
  so the transition after a sensitivity change reported as a new overload
  and the old routine kept stepping three codes per poll, each followed by a
  five-time-constant wait.
- `_ppms_read_sr830_status` now handles the latch: one discarding read after
  every range change, and a fresh double read before acting on an overload
  bit; a transient that has already cleared is reported as
  "OVERLOAD (transient)" and does not move the range.
- `_ppms_autorange_sr830` seeks directly: a confirmed overload probes once at
  the 1 V range (3 τ wait), then jumps straight to the range with 2x headroom
  for the amplitude measured there and verifies it (5 τ); a still-overloaded
  range steps up one code. Above 90 % of full scale the range widens
  directly to 2x headroom; below 10 % for three readings it narrows directly
  (unchanged). From a badly wrong range the proper one is reached in two
  settles instead of many.
- Overloaded readings no longer enter the point average: the acquisition
  keeps sampling and records `overloaded_sample_count`; a point with no
  overload-free reading is refused with the usual output-kept stop.
- Idle autorange still runs only while the 6221 excitation is on. Tests
  updated for the new sequence plus four new tests (probe → target
  convergence, transient overload, proactive up-range, overload exclusion).

## PPMS plots grouped by harmonic order (2026-09-18)

- The PPMS plot area now shows one X / Y / θ row per measured harmonic
  order (1st, 2nd, 3rd) instead of one row per instrument. Every meter set
  to that order — any SR830 slot and the Zurich demodulator carrying it —
  is drawn in the same coordinates: SR830 points as filled circles in the
  slot colour (blue / orange / green), Zurich points as open circles
  (purple), with a legend naming meter and temperature/loop group. Rows for
  orders nobody measures are hidden, so the default configuration
  (SR830-1/2/3 on 1/2/3 and Zurich 1+2+3) gives three rows, each with an
  SR830 and the Zurich overlaid.
- Row titles list the contributing meters, e.g. "1st harmonic X — SR830-1,
  Zurich". Changing an SR830 harmonic or the Zurich mode, or ticking a
  meter in/out of the measurement, regroups the rows immediately.
- Temperature-graded colours still apply inside each meter's series on
  Field/Angle/Current+Temperature scans. The former per-instrument frames
  remain built but hidden, so their titles and the base-page logic are
  unchanged; CSV columns are unchanged.
- The plot frame now clears every previously configured grid row before
  laying out the harmonic rows; a stale weighted row from the old
  per-instrument layout was keeping a blank share of the height below the
  plots. The visible rows share the full plot area equally.
- `test_ppms_two_panels.py` layout tests were rewritten for the harmonic
  rows (including a new same-order overlay test); both offline suites pass.
  Not verified on the Windows PC.

## PPMS page defaults and six-row plot layout (2026-09-18)

- Defaults now match the reference screenshot: PPMS IP 192.168.137.5,
  SR830-1/2/3 harmonics 1/2/3, Zurich harmonic 1+2+3, excitation 1 mA RMS
  at 317.3 Hz, field scan −15000 → 15000 Oe step 1500, settle 0.5 s,
  acquire 1 s, all four meters ticked for measurement.
- Each compact plot row now requests 70 px instead of 150 px
  (`PPMS_PLOT_ROW_MIN_HEIGHT_PX`), so the six X/Y/θ rows (three SR830 plus
  three Zurich harmonics) fit on a 1080 px screen without the last row being
  clipped; rows still stretch equally when the window is taller. The
  "increase window height" guard stays at 55 px.

## PPMS page: third SR830 slot (2026-09-18)

- The PPMS page now has SR830-1 / SR830-2 / SR830-3 (slot keys `830_a` /
  `830_b` / `830_c`). Default addresses are GPIB 8 / 9 / 10; the address
  drop-downs still only accept 8, 9 or 10 and a connect is refused when
  another slot already holds that address.
- The instrument rack gained a SR830-3 cell (third row, next to the Macro
  button), the settings panel a "SR830-3 harm." row with its own Measure
  tick and reference indicator (the Zurich row moved down one row), and the
  plot grid a third row of X / Y / θ frames. Zurich plot rows follow below.
- `validate_ppms_transport_scan_settings` accepts `830_c` and a keyword
  `harmonic_c` (default 1) and returns it as `harmonic_c`; positional
  callers are unchanged. CSV columns remain address based
  (`sr830_<address>_…`), so files gain a third address group only when the
  slot is selected.
- Shared helpers `_ppms_sr830_keys`, `_ppms_harmonic_var` and
  `_ppms_harmonic_value` replace the former two-slot conditionals; loops
  skip slots that are absent, so existing fixtures still run.
- Offline suites: Other-files and test/ green after deselecting the new slot
  in two Zurich layout fixtures. Not verified on the instrument PC.

## Offline test suites green (2026-09-18)

- `test/test_instrument_safety_logic.py`: the PPMS endpoint and STFMR
  default assertions now match the current program defaults
  (192.168.137.5; 4 GHz / 6 dBm), and the fake SR830 answers `SENS?` /
  `OFLT?` so the PPMS autorange path can be exercised; the bare harness
  gets an empty indicator registry.
- `test/test_kepco_4886_standalone.py` skips when the standalone script is
  absent from this folder. `test_stfmr_dual.py` adds `test/` to the path
  for its shared harness.
- Result: Other-files 165/165, test/ 103 passed + 1 skipped. No program
  defaults were changed to make tests pass.

## 2410 / 6221 / Non-local IV / Gate-VI pages: English UI (2026-09-18)

- The remaining 695 Chinese lines were translated: the Keithley 2410 and
  6221 pages, the Non-local IV and Gate-VI (Non-local VI) pages, their
  parameter validation and scan-worker status text, the plot placeholders,
  the zhinst install prompt and the MFLI connect-failure diagnostics.
  `Transport_all_NH_dev.py` now contains no Chinese text.
- The Non-local IV lock-in selection values are now Both / Only #8 /
  Only #9. Instrument commands, interlocks, CSV layouts and the close-down
  order are unchanged. Offline tests updated to the new wording; not
  verified on the Windows PC.

## MOKE pages: English UI (2026-09-18)

- The three MOKE tabs (now "MOKE light", "MOKE motor", "MOKE magnet"), the
  Camera Live window, the K10CR1 analyser stage panel, the Cell-VSDM motor
  window, the LED channel panels, the shutdown dialog and the camera / video
  / magnet / stage helpers are now English (about 750 lines).
- The magnet selector radio buttons and combo box now use the same labels
  as `MAGNET_SWITCH_LABEL_TO_KEY` (Unconfirmed / Out-of-plane / In-plane),
  and the LED mode defaults use `MODE_LABEL_TO_VALUE` (Off / Continuous).
  The 2026-09-17 translation pass had already switched those tables to
  English while the widgets still used the Chinese values, so the selector
  confirmation and LED mode lookups did not match; this restores them.
- Serial commands, Modbus frames, scan sequences, safety interlocks and the
  close-down order are unchanged. Not verified on the Windows PC.

## STFMR / FMR pages: English UI (2026-09-18)

- All user-visible strings on the STFMR lock-in page, the FMR (PNA) page and
  the STFMR instrument controllers / scan recorder are now English (286
  lines plus the StringVar defaults). Labels were shortened where the rows
  were crowded (f start/stop/step GHz, Comp. V, Wait/pt s).
- The KEPCO output-mode values changed from the Chinese labels to Current/Voltage;
  the CURR/VOLT command mapping is unchanged.
- Instrument commands, scan order, discard rules, CSV/log formats and the
  stop / zero / output-off behaviour are unchanged. Offline STFMR regression
  updated for the new wording; not verified on the Windows instrument PC.

## PPMS page: English UI, single PPMS tab, host selection (2026-09-18)

- The former PPMS1 tab is removed. The former PPMS2 page (`PPMS2Panel`) is
  now the only PPMS page, shown as tab "PPMS" and implemented by `PPMSPanel`
  (`PPMS2Panel` remains as an alias). The main app keeps its inert PPMS
  defaults so shared modules can still inspect them, but builds no PPMS
  widgets and no longer runs its own PPMS idle poll timer.
- Every user-visible string on the PPMS code path (UI builder, control and
  scan methods, Zurich MFLI section) is now English. Scan-mode values changed
  from the Chinese labels to Temperature/Field/Angle/Current (and the
  `+Temperature` combinations); they are used as logic keys, in the live CSV
  default file name (`PPMS_<mode>_…`) and in the `scan_mode` column.
  Harmonic labels read "1st/2nd/3rd harmonic".
- Two scan-worker checks that matched the old Chinese controller messages
  (the old ramp-rate and return-code-1 wording) now match the translated
  messages ("must not exceed 1 K/min", "return code 1"); the 9 K boundary
  retry and the SetTemperature return-code-1 handling work again.
- "PPMS IP" is an editable drop-down with 192.168.137.5 and 192.168.137.163;
  the page still defaults to 192.168.137.163 and any other address can be
  typed.
- Instrument commands, scan order, safety interlocks, CSV columns and stop /
  close behaviour are unchanged. Offline PPMS tests were updated to the new
  English keys; remaining failures in `test/` are the pre-existing Chinese
  assertions from the 2026-09-17 translation pass and the two stale-default
  tests. Not verified on the Windows instrument PC.

## PPMS2 Zurich MFLI connect-failure message improved (2026-09-18)

- Symptom: after entering `MF-DEV4414` on the PPMS2 page, the status bar only showed "TCP 8004
  reachable, but the LabOne API handshake failed", with no indication of which step failed and no dialog.
- When a server answers but the device cannot be attached, `connect_mfli` now reads `/zi/devices/visible`
  and `/zi/devices/connected` and puts the devices that Data Server actually sees into the error.
  The first sentence now distinguishes "dev4414 is not on that server (visible devices: …)" (usually
  the other MFLI's built-in 192.168.69.78 server was reached, or the device name is wrong) from "API
  handshake failed (last-step error)"; only the latter suggests installing the LabOne 18.12 environment.
- The hard-coded DEV4435 in the error text was replaced by the requested device name.
- On a PPMS2 connect failure the full diagnostics dialog is shown, as on the Non-local page; the status
  bar still shows only the first sentence. Connection order, API attempt order, configuration commands and disconnect logic are unchanged.
- Added `test_mfli_connect_message.py` (4 tests); the `Other-files` and `test/` offline regressions
  are unchanged. No real MFLI was connected; on the instrument PC check the device name and server
  as the dialog suggests.

## Camera per-frame processing path optimization (2026-09-17)

- Only the per-frame computation of the camera acquisition thread and live preview in
  `Transport_all_NH_dev.py` was optimized; instrument commands, scan order, stop actions,
  CSV/log formats and all default experiment parameters are unchanged; the single-file
  copy-and-run workflow is unchanged.
- The rotated ROI mask is cached by ROI geometry and image size (`_rotated_roi_mask`); the
  two per-frame ROI means (raw and difference) no longer rebuild the coordinate grid; while
  dragging the selection the cache keeps at most 8 entries.
- Added `DriftBackground`: after background acquisition, the float32 and downsampled
  zero-mean templates are generated once; `estimate_drift_shift` /
  `subtract_background_after_drift` accept either raw arrays or prepared objects, and the old
  calling convention is unchanged. The current frame is converted to float32 only once.
- Display binning uses `INTER_AREA` block averaging + `INTER_NEAREST` upscaling when cv2 is
  available, numerically identical to the NumPy path (2048² frame 12 ms → 0.3 ms); without
  cv2 the original implementation is kept.
- `_make_preview` / `difference_preview` now use single-buffer in-place operations; the output
  is pixel-for-pixel identical to the original implementation; `raw_mean` now uses `cv2.mean`.
- The percentile for AUTO contrast of the raw image now uses two-dimensional equal-stride
  sampling (capped at the same 120k samples as the difference image): 2048² frame
  15 ms → 0.5 ms, affects display only, and differs from the exact percentile by
  about 1 count; regions smaller than the sampling cap are still exact.
- End-to-end per-frame time on this machine (Apple Silicon, Python 3.11.9, numpy 2.4.6,
  opencv 5.0) with synthetic 2048×2048 frames: tracked difference 1×1 20.9→12.6 ms, tracked
  difference 2×2 33.3→13.2 ms, raw image AUTO 24.4→8.1 ms. Numbers on the Windows
  experiment machine still need to be measured.
- Added `test_camera_frame_paths.py` (9 items); the offline regression results of the 152
  `Other-files` items and 104 `test/` items are exactly the same as before the optimization
  (1 pre-existing import-path error, 2 pre-existing old-default assertion failures and 2
  pre-existing errors unchanged). No camera or instruments were connected.

## STFMR current–magnetic field relation plot (2026-09-05)

- The STFMR X plot no longer shows the top current scale. The former lock-in R–field plot in
  the lower right is replaced by a plot of 4886 set current vs. LS475 measured magnetic
  field: horizontal axis set current A, vertical axis measured magnetic field G.
- The relation plot uses the `current_a`–`field_g` pairs from the officially saved rows
  directly; different microwave frequencies and Loops form separate lines and are not
  connected across frequencies or Loops. The fourth exported figure is now suffixed
  `_field_vs_current.png`; the X, Y and theta figures are unchanged.
- This change only affects the dev-version display and PNG snapshots; it does not change
  acquisition, live CSV or output shutdown logic. Python 3.11.9 syntax and targeted offline
  regression pass; the actual Windows layout still needs confirmation on the experiment machine.

## STFMR MFLI auto range (2026-09-04)

- Added `MFLI auto range` to the right of the STFMR MFLI time constant, checked by default.
  When a scan starts this option is fixed and the checkbox is disabled, and restored when it
  ends; idle readings do not actively change range.
- Reuses the Signal Input 1 firmware auto-range interface `/{device}/sigins/0/autorange`
  from the other modules, compatible with both the direct API and the Python 3.7 /
  LabOne 18.12 bridge. Per the
  [official node documentation](https://docs.zhinst.com/mfli_user_manual/nodedoc.html)
  this command selects the range by maximum input amplitude; it is not a software rescaling
  of X/Y/R.
- For each microwave frequency, auto range runs once after the starting current is applied
  and before official recording. Afterwards the range is changed on input overload or raw ADC
  utilization ≥85%; when utilization is <15% three consecutive times a smaller range is
  attempted; after a range change there is a three-sample cooldown. When raw extrema cannot
  be read, a lower range is not guessed from a small R value.
- Every range change keeps the current current, discards the readings taken before the
  change, waits `max(per-point wait, 10 × MFLI time constant, 0.2 s)`, then re-reads LS475
  and MFLI. With a 0.1 s time constant the wait is at least 1 s (10 s at the 1 s
  default since 2026-10-14). The existing rule that the
  first two current points of each frequency are not saved is unchanged.
- At most three range changes per point; if the last wait still shows overload/near full
  scale, or the firmware range change times out or the read-back fails, the current point
  is not saved and the existing stop, 4886 zeroing and SMB100A output-off flow is executed.
  This feature adjusts the voltage input range; it cannot fix wiring errors or signals that
  exceed the instrument's maximum input capability.
- CSV adds `mfli_auto_range_enabled`, `mfli_input_range_v`, `mfli_input_utilization`,
  `mfli_auto_range_action`; the log saves the frequency, current point, ranges before/after
  the change and wait time of every `mfli_autorange_retry`. When unchecked, the original
  display/record-overload-status-only behaviour is kept.
- 98 targeted offline tests pass under Python 3.11.9 / Clang 21.0.0, and the embedded bridge
  code passes the Python 3.7 syntax check. No real instruments were connected; the Windows
  layout and real measurements still need acceptance.

## STFMR MFLI input overload indicator (2026-09-04)

- Added an independent `MFLI Overload` indicator to the STFMR control area: red means
  voltage/current input overload, green means a non-overloaded reading was obtained, grey
  means the state is unknown, the read failed, disconnected or switched to SR830.
- Both the direct LabOne API and the embedded Python 3.7 / LabOne 18.12 bridge read
  `/{device}/status/flags/binary` and judge by bit 0 (voltage input overflow) and bit 1
  (current input overflow). Based on the
  [official MFLI node documentation](https://docs.zhinst.com/mfli_user_manual/nodedoc.html).
  Demod sample flags, reference LOCK or the R/range ratio are not treated as input overload.
- Idle: updated with the existing ~0.5 s polling; scanning: updated with every sampled point
  (including the first two discarded points); it is not a high-speed continuous overload
  latch and cannot guarantee capturing brief overloads between two reads.
- Live and manually exported CSVs add `mfli_input_overload` and `mfli_device_status_flags`;
  left empty when unknown/not MFLI. Original historical data is not rewritten.
- The first version only displays and records; the optional auto range of the previous
  section has since been added. When auto range is unchecked, this indicator still never
  automatically stops or changes outputs. The safety logic for normal end, stop and abnormal
  shutdown is unchanged.
- 85 offline regression items pass under Python 3.11.9 / Clang 21.0.0; the bridge code passes
  the Python 3.7 syntax compatibility check. Consistency with the Zurich panel still needs
  verification on the Windows experiment machine.

## STFMR choose directory before start and live recording (2026-09-04)

- In the current self-contained entry `Magneto_Electro_Optical_Microwave_Transport_NH_dev.py`,
  "Start STFMR multi-frequency field sweep" first validates parameters, then chooses the
  save parent directory and confirms the scan; cancelling the selection or the confirmation
  does not start outputs and does not clear the previous result.
- Every scan creates its own `STFMR_field_scan_YYYYMMDD_HHMMSS_microseconds/` subdirectory,
  in which the same-named `.csv` (UTF-8 BOM) and `.log` (UTF-8, one JSON event per line) are
  created and verified writable before configuring the SMB100A/enabling the 4886; old files
  are never overwritten.
- Every valid CSV point is appended, flushed and fsynced immediately before advancing to the
  next current; the original frequency/Loop/current/LS475 field/X/Y/R/θ/reference/range
  fields are kept and a timezone-aware `timestamp_iso` is added. The start-up pre-read of
  each frequency and the first two transition points still do not enter the official CSV.
- The log records scan parameters, resource addresses and connection snapshot, frequency
  switches, start-up dropped points, SR830 auto-range re-reads, and the number of saved
  points and output shutdown results on end/stop/exception/exit. It is a run event and
  parameter log, not an extra instrument query or a full NI bus capture.
- A CSV or log write failure terminates the scan and separately attempts 4886 zeroing and
  SMB100A AM/LFO/RF OFF; log/file close exceptions do not skip the hardware shutdown. Stop
  and normal exit keep the data already written; system crash/power loss remains limited by
  what the operating system and storage hardware guarantee.
- The STFMR control area adds a copyable live save path. "Save CSV + PNG" can still manually
  export the CSV and four PNGs; it is not a prerequisite for live CSV saving.
- Syntax check under Python 3.11.9 and 80 offline regression items (64 instrument safety +
  16 live saving) pass; the Windows layout, real instruments and the experiment machine's
  storage media still need acceptance. In the full-directory test run the old standalone
  KEPCO test cannot run because `KEPCO_4886_control.py` is missing; it is recorded
  separately from this STFMR dev-version test.

## Current entry point and directory navigation (2026-08-17)

The single-file main program under continued development is:

- [`Magneto_Electro_Optical_Microwave_Transport_NH_dev.py`](Magneto_Electro_Optical_Microwave_Transport_NH_dev.py)

The directory is organized by purpose as follows. To protect existing absolute paths and
historical experiment records, this reorganization did not move the roughly 6.5 GB of raw
data and did not delete old scripts.

| Path | Purpose | Recommended use |
|---|---|---|
| `Magneto_Electro_Optical_Microwave_Transport_NH_dev.py` | Current integrated control dev entry | New features are only modified here |
| `MOKE/` | MOKE experiment data, Windows release packages and dedicated tests | See `MOKE/README.md` |
| `PPMS1/` | PPMS1 standalone control scripts and experiment results | Historical reproducible experiment directory |
| `PPMS2/` | PPMS2 standalone control scripts and experiment results | Historical reproducible experiment directory |
| `test/` | Standalone hardware tests, NI Trace and offline regression tests | Not a runtime dependency of the integrated program |
| `PtCoPt-demo/` | Small demo data | Example use |
| `docs/` | Current handover documents and archive notes | Read `docs/README.md` first |

Important principle: experiment data directories, NI Trace and old single-file programs are
only archived, never overwritten; the source code and build scripts inside a release package
must be kept together as a whole.

## New in the current dev version: PPMS1 temperature and magnetic field control (2026-08-17)

- The standalone `Camera Live` tab has been removed; camera connect, disconnect and
  show/collapse entries are merged into the top of the `Light Source Optics Control` tab,
  and the PCO acquisition thread is still not created before connecting.
- A `PPMS1` tab is added at the far right of the main program. The PPMS IP is an editable
  input box, default `192.168.137.105`. The lab's existing topology uses QDInstrument Remote
  Mode by default, default port `11000`; MultiPyVu `5000` is kept as an optional backend.
- Uses the official Quantum Design `SetTemperature`/`GetTemperature` and
  `SetField`/`GetField` interfaces to set and read back temperature and magnetic field live.
  Single-point field supports stepping and zeroing; the field sweep repeats Loops as
  "start→end→start", can be stopped/zeroed and saves CSV.
- Software limits are fixed as: temperature 5–380 K; 10–380 K max 10 K/min, any whole ramp
  touching the 5–10 K interval max 1 K/min; magnetic field max ±89900 Oe, field ramp rate
  must be greater than 0 and below 150 Oe/s. All write commands require the PPMS1 safety
  confirmation to be checked first.
- The PPMS1 tab can separately connect a Keithley 6221, two optional SR830 slots and a
  Keithley 2400. Both SR830 slots can pick an address from the `GPIB0::8/9/10::INSTR`
  drop-down and connect independently, or only one of them may be used; selecting the same
  address in both slots is blocked. The application-level resource lock prevents other tabs
  from opening the same instrument at the same time; outputs are turned off first when
  connecting the 6221/2400.
- The PPMS1 field scan enforces a `6221 AC + at least one SR830` transport measurement; a
  pure PPMS1 field sweep is no longer offered. The GUI sets the 6221 RMS current, frequency,
  compliance voltage and the harmonic of slots A/B; the scan can only start when PPMS1, the
  6221 and at least one SR830 slot are connected. At every stable field point the
  X/Y/R/phase/reference status of the connected lock-ins is recorded by their actual GPIB
  address, and the corresponding `R voltage vs measured field` is plotted live. Normal
  completion turns off the 6221 and returns to the starting field; stop, exception or exit
  turns off the 6221 and requests the PPMS1 field to be zeroed in Driven mode.
- The currently recommended lab link does not require Python on the PPMS host: the PPMS host
  runs MultiVu and `QDInstrument_Server.exe`, and the ThinkPad uses the `QDInstrument.dll`
  from the LabVIEW directory. The following commands are only needed when the MultiPyVu
  backend is selected:

```powershell
py -m pip install --upgrade MultiPyVu
py -m MultiPyVu
```

EverCool II is the helium recovery/cooling subsystem of the PPMS; this program only controls
the basic sample environment through MultiVu and does not send commands directly to the
EverCool II low-level hardware.

## Current dev-version logic consolidation (2026-08-28)

- The main tabs are simplified to `MOKE Light Source`, `MOKE Motor`, `MOKE Magnet`, `2410`,
  `6221`, `IV`, `Gate-VI`, `PPMS1`, `STFMR`.
- A `STFMR` tab is added at the far right of the main program: independently connects the
  KEPCO BOP 4886 20-10 (`GPIB0::6::INSTR`), Lake Shore 475 (`GPIB0::12::INSTR`),
  Keysight N5242A/N5227A PNA (`GPIB0::16::INSTR`), SR830, Zurich MFLI and SMB100A; the 6221
  is not added again on this tab. Currently available: 4886 current/voltage output, 475
  read-only field read-back, and PNA single-frequency FMR scan; SR830/MFLI/SMB100A still only
  perform identity verification and resource occupation. FMR scans the 4886 current directly,
  records the 475 measured field on the horizontal axis, can select the MLOG dB data of
  S11/S12/S21/S22 simultaneously, and saves CSV and a PNG of the current closed-box figure
  window. The PNA performs one channel sweep per field point and then reads all selected
  traces; combinations across source ports are switched by the PNA source port in turn, not
  strictly simultaneous sampling. The 4886 software limits are ±10 A/±20 V; connect,
  exception, disconnect and full program exit all zero and turn off the output; the 475
  keeps the current front-panel unit.
- The Keithley 6221 tab offers two pulse–ROI flows: in the current round-trip scan each
  current point outputs exactly 1 pulse; the fixed-current multi-pulse flow runs continuously
  with the given current and pulse count. Both take the high-level pulse width (us) and the
  post-pulse acquisition/interval time (s) directly and use the mean of the difference ROI
  samples inside that time window as one data point; the right plot shows Pulse current–ROI
  or Pulse number–ROI respectively. With synchronized saving checked, every point saves the
  MOKE raw `uint16 TIFF` at the end of the acquisition window, and the CSV writes pulse
  width, interval, sample count and dispersion alongside. The left plot of the 6221 tab now
  matches the MOKE tab and shows the live `ROI intensity vs time`; the left/right plot widths
  are about 2:1. This item has passed offline regression tests but has not yet entered the
  frozen V3 3.2.0 build package.
- The mirrored round-trip paths for 2410 voltage, 6221 current, MOKE magnet current and
  PPMS1 field are all generated by one hardware-independent numeric function, keeping
  "start→end→start" and exact inclusion of the endpoints; a tiny step is rejected
  immediately against the 100000-point cap before the scan list is allocated, avoiding a
  long unresponsive interface.
- The total exit flow isolates errors per module: camera, LED, rotation stage, magnet, 2410,
  Gate-VI, IV, 6221, STFMR, PPMS1 and motor perform a safe shutdown in turn; an unexpected
  exception in any module does not prevent later modules from receiving the shutdown request.
- Callbacks from background threads to the Tk main thread are now limited to 50 per round
  and batched by a time budget of about 8 ms (a single callback must still run to
  completion); with a backlog, processing continues after 1 ms, without one the 20 ms poll is
  kept, reducing interface stutter when scanning, saving and redrawing happen at the same time.
- The current dev source and offline tests pass the Python syntax check and 50 regression
  items; the V3 release package passes 28 release/instrument logic checks. This only proves
  the numeric paths, resource interlocks, shutdown fault tolerance and simulated instrument
  logic; it does not represent real-hardware acceptance of PCO, serial, GPIB, MFLI or PPMS1.
  The STFMR single-frequency FMR added in this section has not yet entered the frozen V3
  revision 3.2.0.
- All PyVISA controllers in the current dev version share one reference-counted
  ResourceManager; when a single instrument's identity verification fails or it disconnects,
  only that device session is closed, and the shared manager's `close()` is no longer called,
  which caused `VI_ERROR_INV_OBJECT` on the other GPIB devices. The STFMR GPIB connection
  handshakes additionally run serially to avoid concurrent connection races on the same
  interface. The dev version now passes 50 offline regression items; the STFMR
  single-frequency FMR path has completed offline simulation and still needs
  instrument-by-instrument connect/disconnect and scan acceptance on real NI-VISA hardware,
  starting from low current and low compliance voltage.
- Early dev versions tried CR/LF, `*IDN?`, `OUTP?` and a pre-connection wait for the 4886;
  these paths were refuted and disabled by the `STFMR/kepco/Capture.nitrace` provided with
  the project. That LabVIEW record proves that the 4886 uses raw ASCII GPIB writes terminated
  by IEEE-488 EOI with no CR/LF appended, and that the workflow never queries the 4886 at all.
  The current connection only opens the session and neither queries nor writes the device.
- Current mode strictly reproduces the trace: `FUNC:MODE CURR`, zero current/voltage limits,
  auto range, `OUTP ON`, target current. Voltage mode uses the corresponding symmetric
  commands. The 4886 still has no hardware read-back; the GUI shows the cached value last
  sent by this program, and the actual magnetic field is read independently by the
  Lake Shore 475.
- `Scan_GPIB.py` uses a protected mode for GPIB primary address 6: it only reports the 4886
  address as present based on VISA `list_resources()`, never opens a GPIB6 session and never
  sends `*IDN?`. This is because the experiment machine confirmed that repeated identity
  queries make the old 4886 interface stop responding; the other GPIB instruments still
  perform the read-only identity query normally.
- The standalone entry `KEPCO_4886_control.py` has been upgraded to a single-frequency FMR
  program. The 4886 connection still only opens the VISA session and sends no `*IDN?`, device
  clear or any read-back query; manual output and scans use only the raw-ASCII/EOI write
  commands verified by NI Trace. The program can independently connect the Lake Shore 475
  (`GPIB0::12::INSTR`) and Keysight N5242A/N5227A PNA (`GPIB0::16::INSTR`); the operator
  enters start/end/step current directly, the PNA is set to a single CW frequency, and the
  dB results of S11/S12/S21/S22 can be chosen freely. The curve and CSV horizontal axis use
  the 475 measured field rather than a current-to-field conversion; the CSV also saves the
  current setpoint, and the PNG saves the current closed-box curve. Scan completion, stop or
  exception all attempt to zero the 4886 and `OUTP OFF`, optionally turning off the PNA RF.
  This path passes the Python syntax check and the current 50 offline regression items; it
  still needs real-hardware acceptance on the experiment machine starting from low current
  and low compliance voltage.

## Current dev-version camera orientation (2026-08-27)

- Every PCO frame is rotated 90° counter-clockwise before entering the background
  acquisition, difference, ROI, auto contrast, display, recording and saving flows; therefore
  Camera Live, processed images and saved 16-bit images share the same orientation.
- "Raw 16-bit" still keeps the camera's `uint16` pixel values without interpolation or grey
  mapping, but the image width/height axes are swapped by the 90° rotation.
- The four corner labels keep their original physical quadrant identity: after rotation the
  top-left/bottom-left/bottom-right/top-right show `4 / 1 / 2 / 3` in turn; the 100× FOV
  marker has also been converted to the rotated coordinates.
- This orientation update has entered the V3 3.2.0 build package; after the Windows build,
  orientation, corner labels, ROI, difference, recording and both kinds of 16-bit save results
  still must be verified with the real PCO camera.

## Installation dependencies of the current integrated program (by module)

This section corresponds to the current dev entry
`Magneto_Electro_Optical_Microwave_Transport_NH_dev.py`. The main program loads on demand:
when the optional packages or vendor software of one hardware module are not installed, the
other unrelated modules should still start and be usable.

### 1. Python 3.11 base runtime packages

This project additionally keeps a project-level Python 3.11.9 on the current macOS
development machine without replacing the system `/usr/bin/python3`. The local check
environment is in `.venv311`, used as follows:

```bash
cd /Users/nbb/Desktop/2-Posdoc_Heng/0-Codex-Claude/0-Transport-Code
export DYLD_LIBRARY_PATH="$PWD/.pyenv/versions/3.11.9/lib"
export PYTHONPATH="$PWD/.venv311/lib/python3.11/site-packages"
.pyenv/versions/3.11.9/bin/python3.11 --version
cd Other-files && ../.pyenv/versions/3.11.9/bin/python3.11 -m unittest discover -s . -p "test_*.py"
```

`source .venv311/bin/activate` no longer works. The environment was created
in `0-Codex/3-Transport`, and both the `.venv311/bin/python` links and the
library path compiled into the `.pyenv` interpreter still point there. Setting
`DYLD_LIBRARY_PATH` and `PYTHONPATH` as above works around both until the
environment is rebuilt in this folder.

`.pyenv/` is the project's own CPython 3.11.9 runtime and `.venv311/` is the virtual
environment; both are in `.gitignore` and must not be put into the Windows release package.
The current Mac environment only installs cross-platform generic dependencies; PCO SDK,
NI-VISA/NI-488.2, Kinesis, QDInstrument and LabOne etc. still need to be installed and
verified on the corresponding experiment Windows computers.

In the Windows Python 3.11 environment that runs the integrated program, install the generic
dependencies first:

```powershell
py -3.11 -m pip install --upgrade pip
py -3.11 -m pip install pyserial numpy Pillow pco opencv-python PyVISA pythonnet
```

`pyvisa-py` is an optional pure-Python VISA backend:

```powershell
py -3.11 -m pip install pyvisa-py
```

The lab's GPIB instruments preferably use NI-VISA/NI-488.2; after installing NI-VISA there is
usually no need to rely on `pyvisa-py`. The Zurich MFLI `zhinst` version is tightly coupled to
the LabOne version, so it should not be blindly upgraded as part of the generic command above;
see the MFLI section below.

### 2. Module — Python package — vendor software table

| Integrated program module | Required Python packages | Additional software/drivers to install | Notes |
|---|---|---|---|
| Program main interface | No extra pip packages | The Python installation must include Tcl/Tk (`tkinter`) | `csv/json/socket/threading/tkinter` etc. come from the Python standard library |
| Camera Live, MOKE, ROI, image saving | `numpy`, `Pillow`, `pco` | PCO Camera SDK, camera driver and the corresponding native DLLs | Missing any one of the three packages makes the PCO camera module unavailable |
| Drift tracking, mean filter, 8-bit AVI recording | `opencv-python` | No extra vendor program | Without OpenCV the main program starts, but drift-correction acceleration and recording are unavailable |
| Four-channel LED (COM5) | `pyserial` | Corresponding USB serial driver, usually FTDI VCP | 115200, 8-N-1; confirm COM5 in Device Manager before connecting |
| Cell-VSDM motor (COM4) | `pyserial` | USB serial/Modbus adapter driver | 115200, Modbus RTU, address 1 |
| KEPCO/BOP and two gaussmeters (COM12–COM15) | `pyserial` | Actual adapter driver such as FTDI VCP/CH340 | Serial port names may change between computers; verify in the GUI |
| Keithley 2410, Keithley 6221 | `PyVISA` | **NI-VISA**; GPIB communication also needs **NI-488.2** and the NI GPIB driver | `pyvisa-py` can only be an optional backend and cannot replace a missing GPIB hardware driver |
| 6221, 2410, SR830, 2182A in IV / Gate-VI | `PyVISA` | NI-VISA, NI-488.2, GPIB controller driver | Each device connects independently; the application-level resource lock prevents multiple tabs from occupying the same address |
| Thorlabs K10CR1 analyzer rotation stage | `pythonnet` | **Thorlabs Kinesis 64-bit**, bitness must match Python | Default search `C:\Program Files\Thorlabs\Kinesis`; Kinesis provides the MotionControl DLLs |
| Zurich MFLI | See the MFLI section; the main Python can use `zhinst`, old systems prefer the isolated bridge | **Zurich Instruments LabOne**, currently `18.12.59443` in the lab | LabOne Data Server, Python API and instrument firmware/version must be compatible |
| PPMS1 temperature/field control (lab default) | `pythonnet` | The ThinkPad needs `QDInstrument.dll` and .NET Framework 3.5; the PPMS host runs MultiVu and `QDInstrument_Server.exe` | Default connection `192.168.137.105:11000`; the PPMS host does not need Python |
| PPMS1 temperature/field control (optional) | `MultiPyVu` | The PPMS host must install and run MultiPyVu Server | Only for systems where Python may be installed on the MultiVu host; default port 5000 |
| 6221, 1–2 SR830, 2400 on the PPMS1 tab | `PyVISA` | NI-VISA, NI-488.2, GPIB controller driver | SR830 address selectable 8/9/10; MultiPyVu only handles the PPMS sample environment, external instruments still go through VISA/GPIB separately |
| STFMR: 4886, 475, N5242A/N5227A PNA, SR830, SMB100A | `PyVISA` | NI-VISA, NI-488.2, GPIB controller driver | Opens 4886 output, 475 read-back and PNA single-frequency multi-S-parameter FMR; SR830/SMB100A currently only do identity verification; this tab does not include the 6221 |
| STFMR: Zurich MFLI | See the MFLI section | Zurich Instruments LabOne | Currently only establishes the connection and does not write lock-in parameters |

### 3. PCO camera module

Besides the Python packages, the PCO SDK/driver matching the camera and Windows bitness must
also be installed. It is recommended to first confirm with the official PCO program that the
camera can acquire, then run the integrated program. The build baseline currently used by
the dev source is `pco 2.6.0`. If the error mentions `sc2_cam.dll`, recorder, convert or
XCite DLLs, it is a PCO native library/packaging payload problem, not one that reinstalling
`numpy` can solve.

### 4. VISA / GPIB instrument modules

Keithley 2400/2410/6221, SR830 and 2182A share the following base environment:

1. Install NI-VISA.
2. Install the NI-488.2 driver matching Windows and the GPIB controller.
3. Confirm in NI MAX that the resource is visible, e.g. `GPIB0::30::INSTR`.
4. Then install Python `PyVISA` and connect each instrument in the GUI one by one.

Installing `PyVISA` alone does not install NI's GPIB driver; conversely, with only NI-VISA and
no `PyVISA`, Python cannot open these instruments either.

### 5. Thorlabs K10CR1

After installing `pythonnet`, Thorlabs Kinesis must still be installed. The integrated
program loads the Kinesis DLLs on demand, so computers without the rotation stage can use the
other modules without installing Kinesis. With 64-bit Python the 64-bit Kinesis must be
installed; first confirm in the official Kinesis GUI that serial number `55194494` connects
and that Home and the limits behave correctly, then use the integrated program.

### 6. Zurich MFLI / LabOne

The lab MFLI currently corresponds to the old LabOne `18.12.59443`. The recommended route is
the isolated Python 3.7 bridge installation flow provided by the project, letting the main
GUI keep running on Python 3.11; the isolated environment needs exactly `zhinst 18.12.59443`
and `numpy 1.21.6`. Only if switching to a newer LabOne, install the newer `zhinst` matching
the new LabOne in the main Python:

```powershell
py -3.11 -m pip install --upgrade zhinst
```

Do not upgrade only the Python `zhinst` package while still using the LabOne 18.12 Data
Server, otherwise API/Data Server version incompatibility may occur. LabOne itself also
provides the .NET/C API that the program can optionally use; the `.NET` path requires
`pythonnet`.

### 7. PPMS remote interface

#### 7.1 Lab default: QDInstrument Remote Mode

The current physical connection is "PPMS/MultiVu lab computer ↔ Ethernet cable ↔ ThinkPad",
and LabVIEW on the ThinkPad can already control the PPMS. The Quantum Design QDInstrument
remote link should be reused:

1. The lab computer runs PPMS MultiVu normally.
2. The lab computer runs `QDInstrument_Server.exe`; it and MultiVu must use the same
   administrator privilege level.
3. Enable .NET Framework 3.5 on the ThinkPad and install `pythonnet`.
4. On the PPMS1 tab select `QDInstrument remote`, use IP `192.168.137.105` and port
   `11000`.
5. The main program automatically searches for `QDInstrument.dll` by .NET assembly name, the
   program directory and common LabVIEW directories, preferring the LabVIEW 2020 path
   verified on this machine
   `C:\Program Files\National Instruments\LabVIEW 2020\instr.lib\QDInstrument_LabView\QDInstrument.dll`.
   The DLL, LabVIEW VIs and Server should come from the same set of Quantum Design release
   files; the safest deployment is still to copy this set of DLLs into the main program's
   directory.
6. The firewall allows inbound TCP 11000 on the lab computer. The connection log is on the
   ThinkPad at `C:\QDLogs\QDInstrument\Event.log`.
7. Stop the LabVIEW PPMS control VI before using Python, so that two clients do not write
   temperature or field commands at the same time; a single control entry must be kept.

This path does not require Python on the lab computer; Python and `pythonnet` are installed
only on the ThinkPad. The main program calls `SetTemperature(K, K/s, approach)` and
`SetField(Oe, Oe/s, approach, mode)` in the official units; the GUI temperature ramp rate is
in K/min and is converted to K/s before sending. Disconnecting an ordinary client does not
automatically change temperature/field setpoints already submitted; field sweep stop, field
sweep exception or exiting the program during a field sweep requests the field to be zeroed
in Driven mode.

`QDInstrument.dll` from different years has used numeric or enum types for the status
parameters of `GetTemperature`/`GetField`, and they are all .NET `ref/out` parameters. The
main program no longer hard-matches overloads with Python integers; instead it reads the
real parameter types of the current DLL through assembly reflection before calling,
compatible with the `QDInstrument_LabView` component shipped with LabVIEW 2020.

`Test-NetConnection 192.168.137.105 -Port 11000` showing `TcpTestSucceeded : True` only
proves that the remote `QDInstrument_Server.exe` is listening; it does not include the
`QDInstrument.dll` that the ThinkPad needs locally to call that service. If the port is open
but the program still reports a component load failure, look for the DLL in the directory of
the original LabVIEW control VI or its dependency directories, rather than continuing to
modify the firewall or installing MultiPyVu.

#### 7.2 Optional: MultiPyVu

Use this backend only when installing and running Python on the MultiVu computer is
allowed. The client Python needs:

```powershell
py -3.11 -m pip install --upgrade MultiPyVu
```

The PPMS control computer `192.168.137.105` needs:

1. Start Quantum Design PPMS MultiVu normally and confirm MultiVu is connected to the PPMS.
2. Install `MultiPyVu` in a Python environment with the same privilege level as MultiVu.
3. Run `py -m MultiPyVu` and start the Server in its GUI.
4. Windows Firewall allows TCP 5000, or change to another port on both client and server.

The MultiPyVu client and server should preferably be the same version. The current dev
version uses the official `set_temperature()`/`get_temperature()` and
`set_field()`/`get_field()`; EverCool II remains entirely managed by MultiVu.
`pip install MultiPyVu` automatically resolves the dependencies it needs such as `pywin32`,
`pandas`, `Pillow` and `PyYAML`; there is no need to install them one by one.

### 8. Packages only needed to build the EXE

The following packages are only needed when producing the Windows Nuitka release package;
running the `.py` directly does not need them:

| Build package | Purpose |
|---|---|
| `Nuitka` | Compiles the Python main program into a Windows application |
| `ordered-set` | Nuitka build dependency |
| `zstandard` | Nuitka compression/build dependency |

When building the official EXE, use the `requirements_build_*.txt` with locked versions
inside the corresponding release package; do not replace the frozen build manifest with the
generic runtime commands of this section.

## Latest Windows single-file build candidate: V3

- Build package: [MEO_Transport_V3_Windows_Build_Package.zip](MEO_Transport_V3_Windows_Build_Package.zip)
- ZIP SHA-256:
  `129064e2db5dffdac41a2ac3ba40cfef11d096ea0625ad9cf8c8e775ef4abce4`
- Build package directory: [MEO_Transport_V3_Windows_Build_Package](MEO_Transport_V3_Windows_Build_Package/)
- On Windows double-click `BUILD_AND_INSTALL_V3.bat`; the script is fixed to CPython 3.11.9
  x64, Nuitka 4.1.3, MinGW64 and one-file. On success the EXE, EXE SHA-256, PCO self-check
  log and Nuitka compile report are all placed in `V3_Compiled_Output`, and the desktop
  shortcut points directly to the EXE in that directory; `build` only keeps intermediate
  files for diagnostics.
- V3 is frozen from the current
  [Magneto_Electro_Optical_Microwave_Transport_NH_dev.py](Magneto_Electro_Optical_Microwave_Transport_NH_dev.py),
  covering the current light source/camera, motor, MOKE, 2410, 6221, IV, Gate-VI and PPMS1
  tabs; no start-up password.
- Frozen source SHA-256:
  `b90f84e60e284e24ed724b869769e172b85b18b60b679d39bea0d569159b7033`.
- The current package revision is `3.2.0 PY3119 NUITKA413 MINGW64 PCOFIX ROT90 SCANLOG`,
  including the PCO full-flow 90° counter-clockwise orientation normalization, MOKE
  scan/export hardware status logging and the scan start-up status recording fix.
- Syntax check completed on macOS; the dev source passes 27 and the frozen release source
  passes 28 offline regression items. The Windows Nuitka build, post-build PCO self-check and
  real instrument acceptance still must be completed on the lab computer.
- V3 uses the brand-new Transport magnetic field/optics/device icon without a version-number
  mark; the main interface and Camera Live titles end with `-NH & Prof Chen's group`.
- After the build, the application and Python dependencies need only one EXE; vendor runtime
  environments such as NI-VISA, PCO SDK/driver, Kinesis, LabOne and MultiVu/QDInstrument
  services still must be installed.

The [MEO_Transport_V3_Windows_Build_Package.zip.sha256](MEO_Transport_V3_Windows_Build_Package.zip.sha256)
in the same directory can be used for integrity verification after copying to Windows.

## Historical Windows release candidate: V2

- Build package: [MOKE_NH_V2_Windows_Build_Package.zip](MOKE/MOKE_NH_V2_Windows_Build_Package.zip)
- Extracted directory: [MOKE_NH_V2_Windows_Build_Package](MOKE/MOKE_NH_V2_Windows_Build_Package/)
- ZIP SHA-256:
  `6dd1c944ac604c873357b020935a78327a5bb353f05d115aaafa59cbd8faa83b`
- On Windows double-click `BUILD_AND_INSTALL_V2.bat`; on success `MOKE NH Control V2` is
  created on the desktop; V2 has no start-up password and does not overwrite V1.
- V2 has passed syntax, module import, old LabOne bridge source and 8 offline regression
  tests; Windows still needs the Nuitka build, PCO payload self-check and low-risk
  real-hardware acceptance.
- V2 uses a new emerald-green/gold icon with a `V2` badge, distinguishing it from the V1
  blue/pink icon.

The V1 stage notes and historical baseline are kept below.

Stage version: **V1**
Consolidation date: **2026-07-31**
V1 stable version: [MOKE_NH_V1.py](MOKE/MOKE_NH_V1_Lab_Build_Package/MOKE_NH_V1.py)
Subsequent dev version: [Magneto_Electro_Optical_Microwave_Transport_NH_dev.py](Magneto_Electro_Optical_Microwave_Transport_NH_dev.py)

## 1. V1 positioning

`MOKE_NH_V1.py` is the V1 stable snapshot of the experiment system, integrating the camera,
light source optics, sample stage motor, magnet and gaussmeters, Keithley 2410 and
Keithley 6221. The program does not import the old control programs in `test/`, so after
copying `MOKE_NH_v1.py` to the Windows control computer it can run on its own provided the
external drivers and Python dependencies are complete.

`Magneto_Electro_Optical_Microwave_Transport_NH_dev.py` is the current entry for further
development. After the V1 release, `MOKE_NH_V1.py` is frozen in principle; new features and
fixes are first written into the current dev entry and form the next stable version after
testing is complete.

The `test/` directory keeps the original standalone programs and NI I/O Trace; it is the basis
for the serial protocols, parameter scaling and later troubleshooting and should not be
deleted.

## 2. V1 hardware mapping

| Module | Default connection | V1 purpose |
|---|---|---|
| PCO camera | PCO SDK | 16-bit live imaging, ROI, difference and image saving |
| Four-channel LED | COM5, 115200, 8-N-1 | Four-channel light source current and mode control |
| Cell-VSDM sample stage | COM4, 115200, Modbus RTU, address 1 | Init, Home, up/down fine adjustment and absolute position |
| Out-of-plane KEPCO/BOP | COM14 | Out-of-plane magnet current output |
| Out-of-plane gaussmeter | COM13 | Out-of-plane magnetic field reading |
| In-plane KEPCO/BOP | COM12 | In-plane magnet current output |
| In-plane gaussmeter | COM15 | In-plane magnetic field reading |
| Keithley 2410 | `GPIB0::24::INSTR` | Voltage scan, ROI acquisition and resistance measurement |
| Keithley 6221 | `GPIB0::30::INSTR` | DC unipolar pulses of a specified count and current scan |
| Thorlabs K10CR1 | S/N `55194494`, Kinesis | Analyzer rotation stage |

### Physical magnet selector switch

The magnet circuit is selected by the physical selector switch on the instrument; software
cannot replace the mechanical switch:

- After switching to "out-of-plane", the GUI must select out-of-plane, using COM14 for output
  and COM13 for reading.
- After switching to "in-plane", the GUI must select in-plane, using COM12 for output and
  COM15 for reading.
- Both magnet channels must be zeroed before moving the selector switch.
- Ordinary writes only operate on the currently confirmed magnet circuit; emergency zeroing
  tries to write 0 A to both channels separately.

## 3. Completed features

### Camera and ROI

- PCO camera raw data stays `uint16`; the default exposure time is 20 ms.
- Mouse wheel zooms, left-drag pans, `Shift + left-drag` selects a rectangular ROI.
- The ROI supports rotation, width/height adjustment and X/Y translation; the A/W/H/X/Y step
  sizes can all be typed in.
- Auto Contrast is computed only from the picture actually displayed after the current zoom
  and pan.
- Supports difference off, ordinary background difference and drift-tracked difference.
- Supports saving the current raw 16-bit TIFF, and saving images synchronized by
  field/voltage/current during scans.

### Light source optics control

- The four LED channels set current and mode independently; the software current range is
  0–1000.
- The four-channel quick currents are `0 / 10 / 50 / 100 / 500`.
- Mode protocol: off `m=0`, continuous `m=1`, pulsed `m=2`.
- The `ROI intensity vs time` on the light source tab uses the camera's **raw 16-bit ROI
  mean**, without background subtraction and unaffected by the MOKE difference mode switch.
- Integrates the Thorlabs K10CR1 analyzer rotation stage; on program exit communication is
  stopped but the stage is not zeroed, keeping the current analyzer angle.

### Cell-VSDM motor

- Supports Init ON/OFF, Home, Up, Down, absolute position and the 7250 µm quick position.
- The default step size is 0.
- Once checked, the safety confirmation is not automatically cleared after every fine
  adjustment.
- Up/Down do not pop up a completion prompt, for convenient repeated fine adjustment.
- The status register is used to judge the device state and Home, not as a real encoder
  position.

### MOKE field scan

- The magnet current software limit is ±10 A.
- Supports write, reverse, current zeroing, continuous reading of the current channel and
  emergency zeroing of both channels.
- The current channel adds a gaussmeter closed-loop "field zeroing": a small 0.02 A probe
  automatically identifies the field/current direction, each step corrects at most 0.5 A,
  stops within ±1 GS and keeps the compensation current; on no reasonable response,
  communication error, exceeding the iteration limit or emergency stop, the program stops
  the loop and tries to write the current channel back to 0 A.
- Supports "start current → end current → start current" round-trip scans and multiple Loops.
- Every point includes a settling time and an ROI acquisition time.
- The scan confirmation window shows the estimated remaining time; it keeps updating while
  running.
- Records the current setpoint, current read-back, gaussmeter measured Field, ROI
  mean/standard deviation and sample count.
- The Field–ROI plot only adds data during a field scan.

### Keithley 2410

- Supports safe output shutdown, round-trip voltage scans, ROI acquisition and CSV/16-bit
  image saving.
- Supports resistance measurement with automatically selected test current.
- The Voltage–ROI plot only adds data during a voltage scan.
- Shows the estimated remaining time during scans.

### Keithley 6221

- Uses `WAVE:SQUare` to generate 50% duty DC unipolar pulses of `0 A ↔ set current`.
- The pulse width unit is µs, default 10 µs; the pulse count must be entered explicitly.
- Pulse current, pulse width, count and compliance voltage can be set.
- After the rated count the waveform is automatically terminated, zeroed and the output turned off.
- The SCPI sequence follows the 6221 Reference Manual using the waveform parameters,
  `WAVE:EXTR OFF`, `DUR:CYCL`, `WAVE:ARM`, `OUTP ON`, `WAVE:INIT`, and checks the error queue
  and parameter read-back after each command; explicitly disabling the external trigger
  avoids the instrument keeping an old state and waiting for Trigger Link. A finite number of
  microsecond pulses may already be complete and automatically show OFF before the following
  `OUTP?` query; in that case a correct cycles read-back with the remaining parameters means
  the sequence was submitted and completed, and `OUTP=0` alone must not be judged a start failure.
- Supports pulse-current round-trip scans, fixed-current multi-pulse ROI acquisition, CSV and
  synchronized 16-bit image saving.
- The ROI is acquired continuously during the acquisition/interval time after every pulse and
  the mean over that time window is plotted; in fixed-current mode the horizontal axis is
  the pulse index.

## 4. Meaning of the ROI data

| Tab | Source of ROI intensity |
|---|---|
| Light source optics control | Camera raw `uint16` ROI, no background subtraction |
| MOKE | ROI after ordinary difference or drift-tracked difference |
| Keithley 2410 | ROI after difference |
| Keithley 6221 | ROI after difference |

Difference data are floating-point differences and may be negative; the saved current image
and scan-synchronized images are always the camera's 16-bit raw data without display
stretching or background subtraction.

The live curve generates one averaged point every 1 s by default and shows at most 100
points, adjustable in the GUI. The Field/Voltage/Pulse current–ROI relation plots only record
the corresponding scan process, not ordinary standby data.

## 5. V1 Windows runtime environment (historical baseline)

It is recommended to use the Python 3.11 on the current control computer:

```powershell
py -3.11 -m pip install pyserial numpy Pillow pco opencv-python pyvisa pythonnet
```

Additionally required:

- PCO camera driver and SDK.
- NI-VISA or the VISA/GPIB backend actually used in the lab.
- Thorlabs Kinesis, with bitness matching Python.
- The Kinesis DLLs required by the K10CR1 are in the default installation directory.

### Zurich MFLI (DEV4435)

- The lab instrument currently runs LabOne `18.12.59443`, and this version's official Python
  package is only provided up to CPython 3.7, with no CPython 3.11 wheel. Before first use,
  double-click `INSTALL_MFLI_LABONE_18_12.bat` on an internet-connected Windows computer.
  The script only creates an isolated Python 3.7.9 in `%LOCALAPPDATA%\NBB\MOKE_MFLI_PY37`
  and installs the exactly matching `NumPy 1.21.6 + zhinst 18.12.59443`; it does not modify
  the Python 3.11 used by the main program.
- The installation script downloads files from the official Python website and PyPI and
  verifies the SHA-256 of the official 64-bit zhinst wheel
  `f92e32a5766308d2c62311eaf2c8205ca76ed304f6c0511443f4e326ef203040`.
  After `SUCCESS` is shown, close and restart the main program; no Windows restart is needed.
- The program prefers the exact-version bridge above; the main GUI, camera, VISA, magnet and
  other modules all still run on the current Python 3.11. Only if the bridge does not exist
  does it try to load the LabOne C/.NET API or the newer Core in the current Python.
- The connection order is the local LabOne Data Server `127.0.0.1:8004`, then the
  instrument's built-in Data Server `192.168.69.78:8004`.
- Before connecting, a quick TCP check of about 1.2 s is done. If neither port is reachable,
  the program immediately stops the API attempts and prompts to check the USB Device/RNDIS
  link, instead of waiting for LabOne's 20 s timeout.
- The program also looks in the standard directory
  `C:\Program Files\Zurich Instruments\LabOne\API\DotNet` for
  `ziDotNET-win64.dll` and `ziDotNETCore-win64.dll`, so that when the isolated bridge is
  unavailable it uses the client library matching the 18.12 Data Server; only afterwards does
  it try the newer `zhinst.core`.
  The program is also compatible with the already verified, correct-version
  `C:\ProgramData\miniconda3\envs\GMS_VENV_PYTHON\python.exe` environment.
- If the `.NET API` is not installed, the program also searches the LabOne C API and
  LabVIEW's `instr.lib/Zurich Instruments LabOne` directory for `ziAPI-win64.dll`, which the
  current Python calls directly through `ctypes`. The environment variable
  `MOKE_LABONE_C_API` can also be set to the full path of that DLL.
- The main interface status bar shows only a one-line error summary; the full API/DLL search
  diagnostics are kept in the pop-up window and no longer stretch the bottom of the tab.

When the MFLI cannot connect, check in PowerShell first:

```powershell
ipconfig
ping 192.168.69.78
Test-NetConnection 192.168.69.78 -Port 8004
```

For a direct USB/RNDIS connection, the existing working configuration is computer
`192.168.69.77`, instrument `192.168.69.78`. Only after `TcpTestSucceeded : True` proceed to
API/version troubleshooting.

Start:

```powershell
py -3.11 MOKE_NH_v1.py
```

The program title should show `V1`. If the Windows interface has no V1, an old copy is still
running.

## 6. Recommended operating sequence

1. Check magnet cooling, polarity, cables and the current/voltage allowed for the sample.
2. Confirm the position of the physical magnet selector switch and select the same
   out-of-plane or in-plane channel in the GUI.
3. Start the stable version `MOKE_NH_v1.py` and connect the modules to be used one by one.
4. Select the ROI in the camera view.
5. For light-source stability observation, use the raw ROI curve on the light source tab directly.
6. Before MOKE/2410/6221 scans, acquire the background and choose ordinary or drift-tracked
   difference.
7. Verify the scan range, step, Loop, settling/acquisition time and estimated remaining time.
8. When done, use the corresponding zero/off buttons, then exit the integrated program.

## 7. Exit behaviour

When closing the integrated program it attempts to:

- Stop camera acquisition.
- Turn off the four LED channels.
- Stop the analyzer rotation stage and disconnect, without Home and without zeroing.
- Write 0 A to both magnet channels and disconnect.
- Zero the Keithley 2410 and turn off its output.
- Abort the Keithley 6221 waveform, zero it and turn off its output.
- Stop Cell-VSDM motion, perform Init OFF and disconnect.

The exit protection is software protection; it cannot replace the hardware emergency stop,
the magnet power switch and manual confirmation.

## 8. V1 known limitations and items pending verification

- The LED `p/g/d/f` are confirmed to belong to the controller protocol, but their exact
  physical meaning and units are not yet determined; advanced parameters should keep the
  device read-back values by default.
- The LED pulse mode is not yet hardware-synchronized with the camera exposure.
- The actual 6221 pulse width, amplitude and load response should still be double-checked
  with an oscilloscope/current probe.
- The physical magnet selector switch has no position feedback; the GUI selection relies on
  operator confirmation.
- The Cell-VSDM currently has no trustworthy real position encoder read-back; the software
  position is the commanded position.
- The K10CR1 Home and limit behaviour depends on the actual mechanism, Kinesis configuration
  and cable state.
- V1 has passed the Python syntax compilation and module import checks; all hardware
  combinations still need one lab acceptance per module with low-risk parameters.

## 9. V1 trace files

- LED protocol: `test/LED_COM5.nitrace`, `test/LED_COM5_2.nitrace`
- Cell-VSDM: `test/Motor_COM4.nitrace`, `test/Motor_COM4_2.nitrace`
- KEPCO/gaussmeter: `test/kepeco.nitrace`
- Out-of-plane magnet circuit: `test/kepeco_PMA.nitrace`
- In-plane magnet circuit: `test/kepeco_IMA.nitrace`

When features or protocols change later, only modify `MOKE_NH_dev.py`. After stage testing
is complete, create a new stable file starting from **V2**, update the program version number
and add a change record to the README, without overwriting `MOKE_NH_v1.py` and the V1 stage
conclusions.

## 10. Dev-version changes after V1

The following exists only in `MOKE_NH_dev.py` and has not yet been written into the V1
stable snapshot:

- A `Non-local IV` module is added at the far right, combining the Keithley 6221 sinusoidal
  AC output with lock-in readings from two Stanford SR830s (default `GPIB0::8::INSTR`,
  `GPIB0::9::INSTR`). Only #8, only #9 or both may be connected; each SR830 can
  independently select AC/DC input coupling, harmonic and X/Y/R read channel, and the 6221,
  #8 and #9 each have a grey disconnected/green connected indicator. RMS start/end
  current/step, frequency, sampling rate, settling time, per-point measurement time,
  pre-conditioning current/time and compliance voltage can be set. 6221 Trigger Link line 4
  produces an active-low phase marker and the SR830 uses the external reference falling
  edge; the tab shows R–time, theta–time, AC current–voltage (horizontal axis switchable
  I/I²), reference lock, overload/compliance, scan progress and estimated remaining time
  live per lock-in address. Every scan chooses a save parent directory and writes
  raw/summary CSVs with the SR830 address and independent configuration live, and saves
  three figure-window snapshots at the end; on stop, exception or exit the sine wave is
  terminated, the output zeroed and the 6221 output turned off. This tab is mutually
  exclusive with the original `Keithley 6221` DC pulse tab, avoiding two modules occupying
  `GPIB0::30::INSTR` at the same time.

- The `Non-local IV` plot area is fixed at two rows by three columns: the first row shows only SR830 #8's
  R–time, theta–time and I–V, the second row only SR830 #9's; with one lock-in connected, the row of the
  unconnected lock-in stays and shows "not connected". At the end of a scan six plot snapshots are saved
  for the two lock-ins (three when only one is connected). The connection areas of the light source, motor,
  analyser stage, in-plane/out-of-plane BOPs and gaussmeters, Keithley
  2410 and Keithley 6221 also gained grey (not connected) / green (connected) indicators;
  the magnet page shows the state of each of its four serial links separately.

- Once the Cell-VSDM mechanical-path safety confirmation is ticked, Home runs directly without a second prompt;
  if it is not ticked, a dialog warns and Home is refused.
- While an ROI is being drawn in Camera Live, the expensive full-frame display conversion is frozen and only
  the lightweight Canvas outline is updated; the newest camera frames keep arriving in the background, and Live resumes with the newest frame when the mouse is released.
- When saving the three kinds of scan CSV / plot snapshots, saving the current 16-bit image, or choosing a scan-image folder,
  Camera Live only pauses the expensive display conversion; background acquisition and reception of the newest frame continue;
  after completion, cancellation or an error the newest frame is drawn immediately and live display resumes, reducing the risk of a freeze while saving.
- Disk writes of CSVs and 16-bit images use a dedicated file thread that neither occupies the Tk main loop nor
  shares a thread with the LED, motor, magnet, 2410 or 6221 communication queues; other controls stay
  responsive while saving, and the status bar shows "saving in the background".
- Camera Live's Auto Contrast applies to the raw view, plain difference and drift-tracking
  difference alike; difference Auto is computed only from the visible region, and switching to Fixed locks the display centre
  and symmetric range in effect at that moment.
- Difference Auto Contrast now uses a robust median / MAD / core-percentile algorithm over the visible region, which automatically
  removes global brightness offsets and ignores sparse drift-registration edges; weak domain signals of about 1–2 camera counts
  can now use enough of the 8-bit display range. Centre and gain are smoothed in time to reduce flicker;
  this only affects the Camera Live display, not the difference ROI values or saved data.
- A MATLAB-style manual `CLim` live control bar was added above the MOKE `ROI intensity vs time` plot:
  with difference enabled, editing either limit switches to manual mode automatically after a 180 ms debounce
  and updates live, without pressing "Manual" first; Enter and "Manual" still apply immediately.
  "Auto" or the `[A]` key in Camera Live leaves manual CLim. The lower limit maps to
  black, the upper to white, values outside are clipped; this only affects the preview, not the ROI intensity
  or saved data.
- Auto and Fixed modes also show live, in the CLim bar and at the top of Camera Live, the actual
  `[lower, upper]` used for mapping; Auto shows the time-smoothed range actually in effect,
  not the instantaneous statistical candidate that has not been applied yet.
- The CLim bar gained a "Histogram" button that opens a non-modal live histogram window; it summarises the
  floating-point difference values of the currently visible Camera Live region before CLim mapping and shows the actual CLim,
  mean, median, std, P1/P99 and the clipping fractions at both limits. The histogram y-axis uses
  `log(count+1)` and marks 0 and the median.
- The difference data gained 1/2/4/8-frame averaging buttons, 1×1/2×2/4×4/8×8 binning, and an
  independently selectable off/2×2/4×4 sliding mean filter. The processing order is drift correction and background
  subtraction → low-memory running temporal average → non-overlapping block-mean binning → per-pixel sliding mean filter
  → CLim; binning merges neighbouring pixels more strongly, while the mean filter smooths grain
  noise with overlapping neighbourhoods and keeps the image size. CLim is still only the display mapping; the difference, temporal
  average, binning and mean filter before CLim are now shared by Camera Live, the histogram, ROI
  intensity, scan ROI acquisition and "Save processed image". Processed images are float32 TIFFs that keep signed difference values;
  every module keeps its "raw 16-bit" button for saving the full 2048×2048 raw camera frame without background subtraction,
  averaging or filtering.
- The MOKE page gained an exposure-time entry (ms, 0.01–10000 ms allowed) and an "Apply exposure" button;
  the update runs safely on the camera acquisition thread and the value in effect is shown at the top of Camera Live. Changing the exposure
  clears the display average and, with difference enabled, recaptures the background automatically from the first frame at the new exposure.
- When difference is re-enabled after being off, or plain and drift-tracking are swapped, only the old live
  `ROI intensity vs time` points are cleared, the scan data is kept, the original time base is retained
  and the plot jumps to the current elapsed time. Clicking "Recapture BG" or an automatic recapture triggered by an exposure update
  also clears the old curve points, but resets the time base so the next valid difference frame starts near
  `0 s` without waiting for a full refresh interval.
- The MOKE difference page gained an "Add 100× marker" button (Camera Live shortcut `M`); based on the position recorded in the 5×
  overview screenshot, a red circle is overlaid at about 58.9% horizontally and 47.5% vertically of Camera Live,
  its diameter scaled to about 5% of the shorter window side, to estimate the field of view after switching to the 100× objective. The marker
  is only a GUI overlay; it is not written into raw 16-bit or float32 processed images and does not affect ROI data.
- The MOKE page gained Camera Live 8-bit recording: enter the recording FPS and choose an AVI path; disk writes run in the
  background so the camera and instrument control are not blocked; the default is MJPG AVI, which the built-in Windows player opens directly,
  falling back to I420/WMV2/XVID; FFV1, which Windows usually cannot decode, is no longer used.
  8-bit grey frames are copied into three identical channels before muxing, so the picture stays grey. The recording is the current
  Camera Live 8-bit grey view after difference, CLim, averaging, binning,
  mean filter and zoom/pan; ordinary Canvas text, the ROI box, the corner labels
  and the 100× marker are not written into the video.
- Once the physical magnet selector is confirmed, the bottom centre of Camera Live shows the `Gauss` reading of the
  gaussmeter for the selected path, and the same reading is burned into the 8-bit recording; the BOP channel, current and in-plane/
  out-of-plane name are not shown. This marker does not modify raw 16-bit or float32 scientific images.
- The development build pins the right-hand control window to about 43% of the screen width, flush right, with a smaller gap,
  matching the current wide-screen layout reference; on small screens the camera and control panels keep their minimum usable widths.
- The right-hand control window now aligns with the top of Camera Live on the left and has exactly the same height, instead of the earlier
  ~64% short window. The 2410/6221 and live ROI plots gained a bottom x-axis margin; ticks and axis titles
  are on separate lines and the first/last ticks align inwards, avoiding clipping at the window bottom or overlapping text.
- The `ROI intensity vs time` and right-hand relation plots on the MOKE, 2410 and 6221 pages are back to a fixed ~200 px
  height and no longer stretch vertically with the full-height control window; a separate flexible blank row after the status bar
  absorbs the remaining space, left free for future features.
- The raw 16-bit `ROI intensity vs time` on the light-source page is also a fixed 230 px high
  instead of filling the column; with the communication log hidden the reserved bottom row absorbs the extra space, and with it shown
  the space goes to the log area. The 2410 `R vs time` canvas alone grew from 150 px to 220 px.
- The MOKE right-hand loop plot redistributes its top and bottom margins: the scan summary, top-axis Gauss title, Gauss ticks,
  bottom-axis current ticks and current title each occupy their own region; first/last ticks align inwards and a right margin was added,
  avoiding overlapping x-axis text or truncation at the Canvas edge.
- The software input and validation limit of the four-channel continuous LED current was raised from 1000 to 2000; the global entry and
  each channel's current box use the same limit. When first raising the LED current, still test upward from a low value.
- On Windows, when the main window, Camera Live or an entry gains focus, the program selects the installed English
  keyboard layout and closes that control's IME composition, preventing Windows' per-window input-method memory from
  restoring Chinese input; this does not touch other platforms and an IME call failure never
  stops the instrument program from starting.
- The out-of-plane/in-plane manual magnet control rows gained a separate "Step A" entry and `Up`/`Down` buttons,
  default 0.1 A; one step is added to or subtracted from the current target-box value and written immediately, still reusing the physical
  selector, safety tick, remote enable, ±10 A limit and post-write readback verification.
- The Camera Live development build starts in `Contrast FIXED`; only `[A]` switches to
  `Contrast AUTO` driven by the visible region.
- The development defaults of all `ROI intensity vs time` live curves changed to 0.2 s/point and
  at most 100 points (about a 20 s window); the light-source page still uses the raw 16-bit ROI, the other modules
  the difference ROI.
- The analyser stage's default relative angle is now 1°; the UI gained a yellow tip: with the polariser
  at 90°, extinction is best near analyser reading 23°; with the polariser at 0°, near
  12°; fine-tune nearby.
- Saving the MOKE scan CSV also saves a PNG snapshot of the Current / Field–ROI intensity
  plot.
- Keithley 2410 resistance measurement is now a continuous `R vs time`: on start a 1 nA–1 mA test current is selected
  automatically, then samples are taken about every 0.2 s until "Stop measurement"; the x-axis scrolls and
  only the last 20 s are kept in memory. The source current keeps adapting to the voltage during measurement; on stop, emergency
  zero or exit the 2410 output is switched off. The plot y-axis picks Ω/kΩ/MΩ/GΩ automatically and keeps
  the live R, V, I and source-current readings.
- The 2410 `R vs time` auto unit is now decided by the newest resistance value, and the same Ω/kΩ/MΩ/GΩ
  unit is used for the y-axis title, all ticks and the in-plot `Current |R|`. Older large values stay in
  the 20 s curve but no longer cause the mixed case of a MΩ axis with a kΩ live value.
- Keithley 2410 resistance measurement uses the absolute value `|R|` throughout: the live reading, the 20 s
  curve and the retained data no longer show negative resistance; voltage and current readbacks keep their sign,
  which helps checking wiring polarity.
- The two-row Non-local IV plots compute coordinates from each canvas's actual height and keep a fixed bottom x-axis
  margin, so the axis title is not hidden by the next row when the window is squeezed.
- Non-local IV SR830 numeric queries gained GPIB reply synchronisation: a delayed
  `*IDN?` reply is cleared after connecting, queries keep a processing delay; if a numeric query receives an old reply such as the identity string,
  that lock-in's input buffer is flushed and the query retried, and the final error names #8/#9 and the failing command.
- The Non-local IV #8/#9 voltage plots each pick V, mV, µV or nV from the voltage magnitude
  currently shown; the R–time and I–V axis titles, ticks and curves are converted together, while theta–time
  keeps degrees. The two lock-ins' units are independent.
- Saving the Keithley 2410 scan CSV also saves a PNG snapshot of the Voltage–ROI intensity
  plot.
- Saving the Keithley 6221 scan CSV also saves a PNG snapshot of the Pulse current–ROI intensity
  plot.
- On MOKE, 2410 and 6221, "Save CSV" first asks for a folder; the CSV and the matching
  plot PNG are written there together, and each module remembers its last folder for the session.
- The MOKE plot uses the magnet's actual readback current as the bottom main x-axis with 0 A fixed at the centre; the top
  axis shows the corresponding Field (GS) after a linear calibration from this scan's current–gaussmeter data.
