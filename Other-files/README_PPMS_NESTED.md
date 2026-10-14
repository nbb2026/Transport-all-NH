# PPMS1 angle / temperature / field scanning

Active entry: `../Magneto_Electro_Optical_Microwave_Transport_NH_dev.py`.

## GUI

Top readback columns: temperature, field, motor angle/status. Idle refresh uses the existing 1-second timer; reads are sequential, not hardware-synchronized. Failed motor/lock-in reads display unavailable/error rather than fabricated zeros. Two-by-two connection layout: 6221 / SR830-1, 2400 / SR830-2. Internal keys830_a/830_b and address-based CSV channels are retained for compatibility.

SR830-1 and SR830-2 display signed X/Y with automatic V/mV/µV formatting. The existing plot now overlays X/Y, separated by angle/temperature index and field-loop index, to avoid joining different scan conditions. CSV retains X/Y/R/theta/reference metadata and adds target angle, measured angle, target temperature and outer-loop indices. Save CSV remains the existing manual export; this change does not add a real-time disk recorder.

## Order and parameters

Enable angle and/or temperature ranges using the checkboxes. Each range includes both endpoints and may ascend or descend. Angle is outermost, temperature next, existing magnetic-field round trip/Loop innermost. Disabled outer axes are not commanded. For temperature-only or angle-only acquisition, use identical magnetic start/end values (one field point per outer combination). Frequency, RMS bias current, compliance and harmonic settings are captured once at scan start.

Angle range is0–360 degrees. Before each angle, 6221 is turned off and field is sent to zero and checked stable. Movement approaches the angle in <=0.9-degree commands to preserve the verified <=1-degree motor guardrail; each move requires two AtTarget readbacks within0.06 degrees, with cancellation before the next command. This is not an emergency stop: after stop/close, an already submitted motion may continue toward its last small-step target. MultiVu/local control must remain available. No index, coordinate redefinition or motor configuration writes.

Temperature changes occur with6221 off. Software temperature/rate limits are preserved. Physical temperature must be within0.1K and Stable continuously for max(1s,configured settling time), with a4-hour timeout (unchanged on 2026-10-14; only the wait right before each reading became max(Settle s, 7 x the slowest lock-in time constant), see `README.md`). The previously observed SetTemperature return1 is not resent; only physical stable target confirmation permits proceeding. Temperature drift beyond0.1K, field losing stability, or angle deviating/notAtTarget at acquisition aborts instead of saving a nominally valid point.

Source is configured for each angle/temperature field block and closed at its end. Stop/error requests Driven zero field and attempts source OFF; no temperature or motor homing/reset is performed. A successful write does not prove hardware output OFF/field zero when communications fail. Inspect MultiVu and the source after errors.

## Validation and acceptance

Python3.9.6:17 fake-.NET motor tests and5 nested/range/cleanup tests pass. Legacy65-test safety suite still has2 pre-existing default assertion mismatches (.105 vs .5 IP,5 vs4GHz). No Windows GUI-layout or physical nested-scan validation performed.

First accept the layout and idle X/Y against both SR830 panels. Then test one angle, one temperature, one low-risk field point, and save/check CSV. Only after that test a small2x2 outer scan. Confirm field zero before motion, output OFF during transitions, stable temperature, angle readback, two distinct SR830 records and stop behavior. Do not start an unattended wide-angle/temperature scan based on offline tests alone.
