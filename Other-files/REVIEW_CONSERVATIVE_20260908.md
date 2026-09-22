# Conservative review of the main program · 2026-09-08

Subject: `../Magneto_Electro_Optical_Microwave_Transport_NH_dev.py`.
This was a static check plus the existing offline regressions, not a full code audit or hardware acceptance.

## Shared fixes applied

1. The instrument-lease check now treats equivalent GPIB spellings alike, e.g. `GPIB::024::INSTR` and `GPIB0::24::INSTR`, so different modules cannot bypass the mutual exclusion on one instrument.
   Board number and secondary address remain distinct; vendor aliases are not resolved; the address sent to VISA is unchanged.
2. The shared UI callback error log is labelled Transport instead of LED and prints the traceback, making the failing module easier to locate.
   The queue time budget, callback order and shutdown tolerance are unchanged.

## Checked and left unchanged

- Read the shared connection leasing, UI queue and overall exit clean-up order.
- Ran the combined safety-logic tests covering the existing source control, pulse, ROI, lock-in, scan and clean-up checks.
- Ran the MFLI autorange/overload and STFMR live-recording tests.
- Regressed the PPMS motor, four-mode, plot routing and live-save tests.
- No change to instrument commands, ranges, wiring assumptions, scan order, stop actions or default experiment parameters.
- No change to standalone historical scripts, the frozen release package or raw experiment data.

## Verification

Python 3.9.6, offline tests only; no Windows packaging.

| Test | Result |
| --- | --- |
| New shared address-exclusion tests | 3/3 |
| MFLI series | 18/18 |
| STFMR live recording | 20/20 |
| PPMS motor | 19/19 |
| PPMS nested worker/plots | 9/9 |
| PPMS four modes/CSV | 3/3 |
| Combined safety logic | 63/65 |

135/137 in total. The two existing failures first assert the old PPMS IP `.105` (now `.5`) and the old STFMR frequency 5 GHz (now 4 GHz).
No program defaults were changed to make tests pass; the later assertions in the failing tests did not run and cannot be claimed as passing.

## Limits

Tk could not start on this machine, so no GUI rendering was checked; no instruments were connected or operated.
Windows DPI rendering, vendor DLLs, real stop/disconnect and abnormal output-off still need per-module confirmation on the instrument PC.
This round did not split the single file, keeping the copy-and-run workflow on the experiment PC.
