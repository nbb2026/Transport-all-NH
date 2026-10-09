"""Copy into MS-fourState.py the instrument code it shares with
Transport_all_NH_dev.py, so MS-fourState.py runs on its own (user,
2026-10-09: "make it individual").

The copied code goes between the BEGIN and END marker lines in
MS-fourState.py and is replaced on every run: the 6221, SR830 and PPMS
controllers, the rotator, and every constant and helper they use (found by
following names through the main program's top-level definitions).  Two
methods of the main program's page are copied as plain functions.

Run it from the repository root after a fix to those controllers in the main
program, then run Other-files/test_ms_four_state.py:

    python Other-files/tools/ms_fourstate_copy_core.py
"""
import ast
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "Transport_all_NH_dev.py"
TARGET = ROOT / "MS-fourState.py"
BEGIN = "# ===== BEGIN copied from Transport_all_NH_dev.py (Other-files/tools/ms_fourstate_copy_core.py) ====="
END = "# ===== END copied from Transport_all_NH_dev.py ====="

# What MS-fourState.py uses from the main program (as core.<name>).
SEEDS = (
    "short_status_error", "PPMSScanCancelled", "Keithley6221Controller", "PPMSMotorDebug",
    "validate_ppms_field_command", "PPMS_QDINSTRUMENT_BACKEND", "acquire_shared_visa_manager",
    "PPMS_TRANSPORT_REFERENCE_TIMEOUT_S", "PPMS_SR830_RESOURCE_OPTIONS",
    "PPMS_SR830_REFERENCE_SLOPE", "NONLOCAL_SR830_FULL_SCALE_V", "SafeVisaIdentityController",
    "PPMS_TRANSPORT_FREQUENCY_HZ", "PPMS_QDINSTRUMENT_DEFAULT_PORT", "PPMS_PERSISTENT_MARGIN_S",
    "PPMS_PERSISTENT_CONFIRM_S", "PPMS_HOST_CHOICES", "PPMS_FIELD_TOLERANCE_OE",
    "PPMS_FIELD_RELATIVE_TOLERANCE", "PPMS_FIELD_RATE_DEFAULT_OE_S", "PPMS_DEFAULT_HOST",
    "PPMSRemoteReadbackController", "PPMSQDInstrumentReadbackController",
    "NONLOCAL_MARKER_PHASE_DEG", "NONLOCAL_MARKER_LINE", "K6221_DEFAULT_RESOURCE",
)
# Methods of the main program's CombinedControlApp, copied as functions:
# (method, new function name, replacements in its source).
METHODS = (
    ("_ppms_field_is_ready", "ppms_field_is_ready", ()),
    ("_read_ppms_cryogen_display", "read_ppms_cryogen_display",
     (("def _read_ppms_cryogen_display(self):", "def read_ppms_cryogen_display(ppms):"),
      ("self.ppms_environment_controller", "ppms"))),
)


def _defined_names(node) -> list[str]:
    names = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.append(node.name)
    elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names += [n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        names += [(a.asname or a.name).split(".")[0] for a in node.names]
    elif isinstance(node, (ast.Try, ast.If)):
        for inner in ast.walk(node):
            if inner is not node and isinstance(inner, (ast.Import, ast.ImportFrom, ast.Assign,
                                                        ast.FunctionDef, ast.ClassDef)):
                names += _defined_names(inner)
    return names


def copied_block(core_source: str) -> str:
    tree = ast.parse(core_source)
    lines = core_source.splitlines()
    definitions: dict[str, list[int]] = {}
    for index, node in enumerate(tree.body):
        for name in _defined_names(node):
            definitions.setdefault(name, []).append(index)
    method_nodes = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "CombinedControlApp":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name in {m[0] for m in METHODS}:
                    method_nodes[item.name] = item
    pending = list(SEEDS) + [
        n.id for item in method_nodes.values() for n in ast.walk(item) if isinstance(n, ast.Name)
    ]
    needed, seen = set(), set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        for index in definitions.get(name, ()):
            if index not in needed:
                needed.add(index)
                pending += [n.id for n in ast.walk(tree.body[index]) if isinstance(n, ast.Name)]
    parts = []
    for index in sorted(needed):
        node = tree.body[index]
        start = node.lineno - 1
        if getattr(node, "decorator_list", None):
            start = node.decorator_list[0].lineno - 1
        parts.append("\n".join(lines[start:node.end_lineno]))
    for method, function, replacements in METHODS:
        node = method_nodes[method]
        text = "\n".join(line[4:] for line in lines[node.lineno - 1:node.end_lineno])
        for old, new in replacements:
            if old not in text:
                raise SystemExit(f"{method}: {old!r} not found; update METHODS")
            text = text.replace(old, new)
        text = text.replace(f"def {method}(", f"def {function}(", 1)
        parts.append(f"# CombinedControlApp.{method} of the main program, as a function.\n" + text)
    digest = hashlib.sha256(core_source.encode("utf-8")).hexdigest()[:12]
    header = (
        f"{BEGIN}\n"
        "# The instrument code MS-fourState shares with the main program, copied so\n"
        "# this file runs on its own.  Do not edit here: fix the main program and run\n"
        "# Other-files/tools/ms_fourstate_copy_core.py.\n"
        f"# Copied from Transport_all_NH_dev.py, SHA-256 {digest}…\n"
    )
    return header + "\n\n\n".join(parts) + f"\n{END}"


def main() -> None:
    target = TARGET.read_text(encoding="utf-8")
    if target.count(BEGIN) != 1 or target.count(END) != 1:
        raise SystemExit(f"{TARGET.name} needs exactly one BEGIN and one END marker line")
    head, rest = target.split(BEGIN, 1)
    _old, tail = rest.split(END, 1)
    TARGET.write_text(head + copied_block(CORE.read_text(encoding="utf-8")) + tail, encoding="utf-8")
    print(f"{TARGET.name}: the copied instrument code is refreshed")


if __name__ == "__main__":
    main()
