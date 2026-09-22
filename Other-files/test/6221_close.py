import time
import pyvisa


KEITHLEY_6221_ADDR = "GPIB0::30::INSTR"


def write(inst, cmd):
    inst.write(cmd)
    time.sleep(0.05)


def main():
    rm = pyvisa.ResourceManager()
    src = rm.open_resource(KEITHLEY_6221_ADDR)

    src.timeout = 5000
    src.write_termination = "\n"
    src.read_termination = "\n"

    try:
        identity = src.query("*IDN?").strip()
        if "6221" not in identity.upper():
            raise RuntimeError(
                f"Refusing to send shutdown commands to unexpected instrument: {identity}"
            )

        # Stop the 6221 waveform output
        errors = []
        for command in (
            "SOUR:WAVE:ABOR",
            "CURR 0",
            "OUTP OFF",
            "*RST",
            "*CLS",
        ):
            try:
                write(src, command)
            except Exception as exc:
                errors.append(f"{command}: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))

        try:
            output_state = src.query("OUTP?").strip()
            print(f"6221 OUTP? = {output_state}")
        except Exception:
            pass

        print("Keithley 6221 output turned OFF.")

    finally:
        src.close()
        rm.close()


if __name__ == "__main__":
    main()
