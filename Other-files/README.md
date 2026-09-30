# Magneto-Electro-Optical Transport Experiment Control Directory

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
  and MFLI. With a 0.1 s time constant the wait is at least 1 s. The existing rule that the
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
