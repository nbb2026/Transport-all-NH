import pyvisa


TIMEOUT_MS = 3000


def setup_inst(inst):
    inst.timeout = TIMEOUT_MS
    inst.write_termination = "\n"
    inst.read_termination = "\n"


def safe_query(inst, cmd):
    try:
        response = inst.query(cmd).strip()
        return True, response
    except Exception as e:
        return False, str(e)


def test_address(rm, addr):
    print("=" * 70)
    print(f"Address: {addr}")

    try:
        inst = rm.open_resource(addr)
        setup_inst(inst)
    except Exception as e:
        print("Open: FAILED")
        print(f"Reason: {e}")
        return

    print("Open: OK")

    try:
        inst.clear()
    except Exception:
        pass

    ok, identity = safe_query(inst, "*IDN?")
    if not ok:
        short_error = identity.splitlines()[0]
        print(f"{'*IDN?':<18} -> FAILED: {short_error}")
        print(
            "Result: identity unavailable; no model-specific queries were sent."
        )
        inst.close()
        return

    print(f"{'*IDN?':<18} -> {identity}")
    identity_upper = identity.upper()

    if "SR830" in identity_upper:
        # SR830 does not implement Keithley SYST:ERR?. Use only SR830 queries.
        inst.write("OUTX 1")
        commands = ["OUTP? 1", "OUTP? 2", "OUTP? 3", "OUTP? 4",
                    "FREQ?", "HARM?", "FMOD?", "LIAS?"]
    elif "6221" in identity_upper:
        commands = ["OUTP?", "SOUR:WAVE:FREQ?",
                    "SOUR:WAVE:AMPL?", "SYST:ERR?"]
    elif "2410" in identity_upper:
        commands = ["OUTP?", "SOUR:FUNC?", "SOUR:VOLT?",
                    "SOUR:CURR?", "SYST:ERR?"]
    elif "2182" in identity_upper:
        commands = ["SENS:FUNC?", "SENS:CHAN?", "SYST:ERR?"]
    else:
        commands = []
        print("Unknown model: no device-specific queries were sent.")

    any_response = True
    for cmd in commands:
        ok, response = safe_query(inst, cmd)

        if ok:
            any_response = True
            print(f"{cmd:<18} -> {response}")
        else:
            short_error = response.splitlines()[0]
            print(f"{cmd:<18} -> FAILED: {short_error}")

    if any_response:
        print("Result: DEVICE RESPONDED")
    else:
        print("Result: NO VALID QUERY RESPONSE")

    try:
        inst.close()
    except Exception:
        pass


def main():
    rm = pyvisa.ResourceManager()

    print("Available VISA devices:")
    resources = rm.list_resources()

    if resources:
        for addr in resources:
            print(f"  {addr}")
    else:
        print("  No VISA devices found")

    print("\nTesting devices found by list_resources():")

    for addr in resources:
        test_address(rm, addr)

    rm.close()


if __name__ == "__main__":
    main()
