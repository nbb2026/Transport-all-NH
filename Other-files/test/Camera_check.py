import logging

import pco


def main():
    logging.getLogger("pco").setLevel(logging.INFO)
    logging.getLogger("pco").addHandler(pco.stream_handler)

    try:
        with pco.Camera() as cam:
            cam.default_configuration()

            print("PCO camera OK")
            print(f"camera name : {cam.camera_name}")
            print(f"serial      : {cam.camera_serial}")
            print(f"interface   : {cam.interface}")
            print(f"is color    : {cam.is_color}")
            print(f"description : {cam.description}")

    except Exception as exc:
        print("PCO camera check failed")
        print(f"error: {exc}")


if __name__ == "__main__":
    main()