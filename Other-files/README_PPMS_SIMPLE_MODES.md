# PPMS four single-variable scan modes

Entry: `../Magneto_Electro_Optical_Microwave_Transport_NH_dev.py`.
This page replaces the old nested angle/temperature/field scan entry; historical data is untouched.

- Temperature: holds the angle, field and current set above; sweeps the temperature range one way.
- Field: holds the angle, temperature and current set above; sweeps the field range one way.
- Angle: holds the temperature, acquisition field and current set above; sweeps the angle range one way.
- Current: holds the angle, temperature and field set above; sweeps the AC RMS current range.

After choosing a mode, enter start, end, a positive step and the settle time per point. Descending ranges are detected automatically.
Since 2026-10-14 each reading waits max(Settle s, 7 x the slowest lock-in time constant measured), 7 s at the 1 s defaults; the confirmation shows the wait and the estimate counts it (see "RT-E and PPMS: lock-in readings settle at least 7 x TC" in `README.md`).
Switching mode resets that mode's suggested range; check it before starting. Non-scanned quantities use the values in the direct-setting row above.
Both SR830s and the 6221 must be connected; each SR830 can use harmonic 1/2/3; acquisition and CSV keep the raw X/Y channels.
The plot shows both SR830s' X/Y against measured temperature, measured field, measured angle, or the configured and verified RMS current.

The current is sinusoidal AC RMS in mA, must be greater than zero, and is bound by the existing 6221 peak/compliance limits.
This is not a DC current scan and resistance is not computed automatically; voltages keep their raw units.
"Set I" enables the output and verifies both lock-in references; "I off" stops the output.

Before a rotation the current is switched off and the field zeroed; the motor still moves in 0.9° segments; the acquisition field is restored afterwards.
"Fixed field" therefore means the acquisition condition, not a non-zero field held while the motor turns.
Acquisition starts only after the temperature is stable; angle and temperature are checked again at acquisition. Errors/stops keep the existing current-off and requested field-zero sequence.
A normal end switches the current off and the environment holds the last point. Manual motor moves are strictly below 30° each; there is no software motor emergency stop.

The upper settings area has a limited height and scrolls vertically; the plot area keeps its own space.
2026-09-07: 27 offline tests passed under Python 3.9.6 (four mode entries, current scan, cancellation and motor tests).
Tk failed to start on this machine, so no visual acceptance was done; Windows rendering and real behaviour need confirmation on the experiment PC.
