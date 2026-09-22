# Standalone Hardware Tests and Protocol Tracing

This directory is not a runtime dependency of the integrated GUI; it is mainly used for single-instrument verification and protocol tracing.

## Categories

- Camera: `Camera_*.py`
- Keithley 2400/2410/6221 and SR830: `.py` files named after the corresponding model
- Keithley 2182A: `2182_read_voltage.py`
- Magnet and gaussmeter: `KEPCO_Gauss_control.py`; `kepeco*.nitrace`
- LED: `LED_control.py`; `LED_COM5*.nitrace`
- Motor: `Motor_control.py`; `Motor_COM4*.nitrace`
- Thorlabs: `Thorlabs_control.py`
- VISA enumeration: `Visa_list.py`; `visa_device_check.py`
- Offline regression: `test_instrument_safety_logic.py`

`.nitrace` files are the key raw evidence for confirming the serial protocols and must not be compressed, overwritten, or deleted. The old handoff document has
been moved to `../docs/archive/HANDOFF_TEST_SCRIPTS_LEGACY.md`.
