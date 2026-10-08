# Transport-all-NH

Instrument control and acquisition for magneto-electro-optical transport
experiments: magneto-optic Kerr imaging, harmonic Hall transport in a PPMS,
room-temperature electrical measurement, and ferromagnetic and spin-torque
ferromagnetic resonance.

Everything runs from one Tkinter application,
[`Transport_all_NH_dev.py`](Transport_all_NH_dev.py). A single process owns the
whole shared rack, so one page cannot take an instrument another page is
already driving, and every source is brought to a safe state on shutdown.

## Running it

```bash
python3 Transport_all_NH_dev.py
```

Python 3.11 is what the program is developed and tested against. Every
hardware backend is imported lazily, so the application opens with none of
them installed and a page whose driver is missing reports that rather than
stopping the rest from working.

| Backend | Needed for |
| --- | --- |
| `pyvisa` | Every GPIB instrument |
| `numpy`, `opencv-python`, `Pillow` | Camera frames, region statistics, drift correction |
| `pco` | PCO camera acquisition |
| `zhinst` | Zurich Instruments MFLI lock-in |
| `pyserial` | LED controller, Cell-VSDM sample stage, KEPCO serial paths |

## Pages

The notebook has ten pages, grouped by rack.

**MOKE.** `MOKE light` drives the four-channel LED illumination and holds the
camera connection and live view. `MOKE motor` moves the Cell-VSDM sample
stage. `MOKE magnet` runs the KEPCO bipolar supply against a gaussmeter and
shows the difference image and its region of interest. `MOKE-24` and
`MOKE-6221` are the Keithley source-measure and current-source pages; the
MOKE-24 page drives a 2410 or a 2400, chosen on the page, for its voltage
scan against the region of interest, and the 6221
page does both one-pulse-per-current round trips and fixed-current multi-pulse
acquisition, averaging the region of interest over the post-pulse interval.

**Non-local** has one instrument rack (Keithley 2410 or 2400, 6221, two
SR830s, the MFLI and a 2182A) and three sub-tabs that use it:
- **Gate-VI pulse scan** measures non-local voltage against a gate, with the
  Keithley as the gate source and lock-in or nanovoltmeter detection.
- **|R| vs time** measures the sample resistance continuously with the
  Keithley as an autoranged current source (1 nA to 1 mA, 5 V protection),
  plotted over the last 20 s. It was on the MOKE-24 page until 2026-10-08.
- **I–V sweep** steps the Keithley voltage once from Start to End (default
  −6 to 6 V in 0.25 V steps, ±20 V at most, 20 V OVP) and plots the current
  read at each step. It saves a CSV, the plot as a PNG and a parameter log.

Only one of the three drives the Keithley at a time. The 6221 is switched off
before |R| or I–V starts, and the Keithley is zeroed and switched off when
they end.

**PPMS** controls temperature, field and rotation, and runs harmonic Hall
transport with up to three SR830 lock-ins and the MFLI. Meters measuring the
same harmonic share one plot. Selecting `1+2` or `1+2+3` on a meter runs the
scan once per harmonic order.

**RT-E** is the same transport measurement at room temperature, with the field
from a KEPCO supply read back by a Lake Shore gaussmeter.

**FMR** sweeps field against a Keysight PNA network analyser. **STFMR** sweeps field with a
microwave source and lock-in detection, with MFLI, SR830 or both together.

## Instruments

Keithley 2410, 2400, 6221 and 2182A; Stanford Research SR830 lock-ins;
Zurich Instruments MFLI; KEPCO BOP bipolar supplies; Lake Shore gaussmeters;
Rohde & Schwarz SMB100A; a Keysight PNA network analyser; Quantum Design PPMS
through either QDInstrument or MultiPyVu; PCO cameras; Thorlabs K10CR1
rotation; and serial LED and sample-stage controllers.

Default VISA addresses are set in the code and editable in the interface.

## Layout

| Path | Contents |
| --- | --- |
| `Transport_all_NH_dev.py` | The application |
| `Other-files/test*.py`, `Other-files/test/` | Offline regression tests |
| `Other-files/README.md` | Dated change log, newest first |
| `Other-files/docs/` | Generated user manual |
| `Other-files/MEO_Transport_V3_Windows_Build_Package/` | Frozen Windows release |
| `Test_Microwave_NH_dev.py` | Standalone fixed-current microwave x modulation grid test (`Other-files/Test_Microwave_NH_dev_README.md`) |
| `MS-fourState.py` | PPMS state writing and zero-field SR830 read: a cycle such as D-A-B-E-B-A repeated, or a typed sequence, plus manual angle, field and current settings as on the PPMS page; uses the main program's instrument code (`Other-files/MS-fourState_README.md`) |

## Tests

The tests run with no hardware attached. Instrument access is stubbed, so they
cover acquisition logic, safety interlocks, scan scheduling and plotting rather
than real measurement.

```bash
cd Other-files && python3 -m unittest discover -s . -p "test_*.py"
```

```bash
cd Other-files/test && PYTHONPATH=../.. python3 -m unittest \
  test_instrument_safety_logic test_mfli_autorange test_mfli_overload \
  test_stfmr_realtime_recording
```

That is 381 and 104 tests respectively, all passing as of 2026-10-04.
On macOS they hang while the display is asleep, because Tk waits for the
window server; wake it first, or run them under `caffeinate -u`.

The same tests, plus layout checks with a screenshot of each page, run on
GitHub's Linux and Windows machines on every push to `main`
([`.github/workflows/gui-tests.yml`](.github/workflows/gui-tests.yml),
[`Other-files/ci/gui_checks.py`](Other-files/ci/gui_checks.py)). Results and
screenshots are on the repository's Actions tab. Running them there keeps
test windows off the desk computer's screen.

Passing tests are not hardware acceptance. They build panels from stubs and
never run the real constructor, so a change should also be checked by starting
the application and opening every page.

## Notes

If Connect PPMS fails with a message about loading `QDInstrument.dll`, look in
it for `EndpointNotFoundException` or TCP error 10060/10061: that is a network
fault, not the DLL. Check the PPMS computer's address with `ipconfig` and the
port from the measuring PC with `Test-NetConnection <PPMS IP> -Port 11000`.
`Other-files/README.md` (2026-10-06) has the PPMS1 case and its fix.

Older LabOne releases expose the MFLI only through a Python 3.7 API. When the
installed `zhinst` cannot be imported directly, the program looks for a Python
3.7 interpreter with `zhinst` 18.12 and talks to the instrument through it as a
subprocess.

The development machine keeps a project-local CPython under `.pyenv` with a
`.venv311` environment. Both are ignored here and excluded from release
packages. That environment was created at an earlier path, so its activation
script no longer works; `Other-files/README.md` records the invocation that
does.

## License

GPL-3.0. See [LICENSE](LICENSE).
