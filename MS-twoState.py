"""MS-twoState: switch between any two states, for example A-B-A-B-...,
until Stop or for a set number of pairs, reading each at zero field.

This is the MS-fourState window in its two-state mode: the same instruments,
steps, stops, plot and data files (Other-files/MS-fourState_README.md).
MS-fourState.py and Transport_all_NH_dev.py must be in the same folder.

    python MS-twoState.py
"""
import importlib.util
import sys
from pathlib import Path


def load_four_state():
    path = Path(__file__).resolve().with_name("MS-fourState.py")
    if not path.is_file():
        raise SystemExit(f"MS-fourState.py must be in the same folder as {Path(__file__).name}")
    spec = importlib.util.spec_from_file_location("ms_four_state", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    load_four_state().main("two")
