import pyvisa


def main():
    rm = pyvisa.ResourceManager()
    try:
        resources = rm.list_resources()

        for addr in resources:
            print("\nAddress:", addr)
            inst = None
            try:
                inst = rm.open_resource(addr)
                inst.timeout = 2000
                inst.write_termination = "\n"
                inst.read_termination = "\n"

                try:
                    print("IDN:", inst.query("*IDN?").strip())
                except Exception as e:
                    print("IDN query failed:", e)
            except Exception as e:
                print("Open failed:", e)
            finally:
                if inst is not None:
                    inst.close()
    finally:
        rm.close()


if __name__ == "__main__":
    main()
