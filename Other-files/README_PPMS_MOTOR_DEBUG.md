# PPMS1 motor debug interface

## Current completion handling

User hardware test: MOVE target26 degrees returned Int32(1), but MultiVu stopped at26.015 and the program read26.0148/AtTarget. Motor MOVE only now treats Int32(1) with empty reply/error strings as ambiguous submission, not immediate failure or unconditional success. No changes to temperature/field return-code handling. The worker polls every0.5s up to90s and requires two consecutive AtTarget reads within0.06 degrees. Verified completion clears the pending GUI gate and explicitly retains return1 in status/log. Other return codes, nonempty replies, bad reads, limit/index/failure/unknown state, cancellation and timeout remain errors without resending. The GUI stays responsive but PPMS commands remain serialized while verification runs. Cancel/close does not stop physical motion. 17 offline tests passed on Python3.9.6; this automatic verification revision is not yet hardware-tested.

## Current: normal MOVE enabled for small-step acceptance

Supersedes the disabled/read-only stage and original DynaCool probe below.
User confirmed GetPPMSItem(3) angle 25.0040 degrees matches MultiVu.
Normal motion now sends exactly `MOVE <angle> 0 <slowdown>` through the supplied five-argument reflected SendPPMSCommand signature, with empty ref strings and final arguments Int32(0), Double(0). This sequence follows the original implementation at https://github.com/guenp/PyQDInstrument/blob/master/pqdinstrument.py; QD's application note supports the SendPPMSCommand route. No index/coordinate-definition mode is exposed.

GUI now labels **Decel 0–14**, default **14 (slowest)**; 0 is fastest, not zero speed. Before sending, channel 0's general status word is read and position bits 12–15 must equal 1 (stopped at target). Codes 5/8/9/15 indicate moving/limit/index/failure; unknown or failed status reads block movement while still allowing angle display. Mapping source: QD TTO manual table B-1 p.B-3, https://www.nanophys.kth.se/nanolab/ppms/4307-004%20Dynacool%20Digital%20User%20Manual%20Set/12%201684-100%20Rev%20B0%20TTO%20Option%20User%27s%20Manual.pdf . Status-channel behavior still needs hardware verification.

First test: read angle/status, require AtTarget, use target **25.5 degrees** only if fresh position is still 25.004 degrees, slowdown14, confirm both safety boxes and send once. Inspect MultiVu, then manually click read angle. Movement completion is not inferred from command return. New PPMS writes remain blocked until AtTarget and angle within 0.06 degrees of requested target are read. If an error happened before any attempted write, the pending block is released; uncertain write errors/timeouts keep it. No retries. No software emergency stop; keep the independent local procedure available. No hardware limit configuration or coordinate redefinition. 0–360 and max1-degree step limits remain.

13 fake-.NET tests pass on Python3.9.6, including malformed status, exact command/arguments, nonempty reply and timeout single-send behavior. No real movement was performed here. Old acceptance instructions below are historical; do not use their native-Speed/DynaCool settings.

## Correction after actual DLL inspection

The high-level GetPosition call returned Int32 1 on the user's PPMS. QD application note 1070-210 C0 p.5 explicitly restricts the high-level position subsystem to DynaCool. **The original movement instructions below are superseded: movement is now disabled.** Read angle now uses the user-confirmed `GetPPMSItem(Int32, Double ByRef, Boolean)` signature with channel 3 and update True (channel documented on p.6). It reports angle only, not stopped state. First compare with MultiVu's 25.004-degree readback. SendPPMSCommand motion needs further parameter/command verification before activation. Existing temperature and field controls remain unchanged. Offline tests check channel/type marshaling and rejection of errors; no hardware acceptance yet.

Entry: `../Magneto_Electro_Optical_Microwave_Transport_NH_dev.py`, PPMS → PPMS rotation motor (small-step debug).

## Evidence and limits

`../motor.nitrace` (SHA256 `321e361b612570d5c0a91be94019a080f610c5c5a40df421cfd757e61fdb4f08`) contains readable VISA resources GPIB30/8/10 and 6221/SR830 identities. No motor command was identified. The trace is preserved unchanged. This is NOT a trace-reconstructed driver.

The experimental implementation probes the loaded QDInstrument assembly's high-level `GetPosition(string, ref value, ref status)` and `SetPosition(string, double, double, enum)` interfaces with axis `Horizontal Rotator`. PPMS/DLL compatibility is unconfirmed. Missing methods, unknown modes and unknown status names fail closed. There is no automatic fallback to raw MOVE, no index search, coordinate redefinition, calibration change, or automatic angular scan.

Quantum Design describes a separate Model 6000 route via SendPPMSCommand_Rotator.vi in https://qdusa.com/siteDocs/appNotes/1070-210.pdf (p.7). That route is not implemented without matching parameter details. If the high-level API is not supported by this PPMS, provide the interface information and the LabVIEW VI/DLL so the correct route can be added.

## First Windows acceptance

1. Use the updated main Python script and existing working QDInstrument connection. Do not run another automated motor controller simultaneously. Keep MultiVu Motion Control accessible.
2. Click **Interface info**. Copy the full text (method signatures and enum names) for diagnosis. This action does not move the motor. The same information goes into the application's communication log.
3. Click **Read angle** and compare both angle and status with MultiVu. A command return code is not an angle or a motion-completed flag. A failed/unsupported read prevents movement.
4. **Do not guess the Speed unit.** The field is intentionally blank and labelled as the DLL native parameter, not degrees/second. Verify its meaning against the matching DLL documentation/LabVIEW implementation before checking the motor-specific confirmation. GUI temporarily accepts only finite values in (0,1]; this numerical cap does not establish physical speed safety.
5. With wiring clearance, angle calibration and speed verified, enable the existing PPMS safety confirmation plus motor-specific confirmation. Enter a target within 1 degree of the fresh readback (e.g. current +0.5 degrees), within the debug software envelope 0–360 degrees. These are conservative software limits, not detected hardware limits.
6. The code checks fresh status and target, resolves a named move-only enum, and invokes SetPosition at most once. A successful return means submitted, not completed. Confirm actual movement in MultiVu and click **Read angle** again. The application blocks new PPMS set/scan commands until an accepted stopped status is read. On any ambiguous return, do not repeat the move; inspect MultiVu first.

## Stop / disconnect

This debug interface has **no software emergency-stop implementation**. Do not use it when an independently accessible local stop procedure is unavailable. Motion is a single bounded target command, not continuous jog. Closing/disconnecting the Python client does not guarantee the motor stops, does not home it, and does not reset its coordinate. Handle abnormal motion using the lab's MultiVu/local stop procedure. A timeout or transport error cannot prove a motion was not executed.

## Offline validation

Python 3.9.6 import and 10 fake-.NET motor tests passed; no vendor DLL was loaded and no physical instrument was contacted:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 3-Transport-code/Other-files/test_ppms_motor_debug.py
```

The 65-test existing safety suite remains at 63 passes / 2 old-default assertion failures (PPMS .105 vs current .5; STFMR 5 GHz vs current 4 GHz). Offline tests are not Windows, GUI-layout, DLL, speed-unit, or mechanical acceptance.
