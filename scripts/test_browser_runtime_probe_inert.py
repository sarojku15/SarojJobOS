#!/usr/bin/env python3

"""
Regression test confirming scripts/naukri_browser_runtime_probe.js
(a diagnostic-only tool, launched manually and never automatically) is
NOT referenced anywhere in the production Naukri/worker code path.
Mirrors the existing project convention of keeping exploratory
naukri_*_probe.js scripts structurally separate from
naukri_fetcher.py/naukri_adapter.py/search_worker.py.

Makes no network request, launches no browser -- static text check only.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE_NAME = "naukri_browser_runtime_probe.js"

PRODUCTION_FILES = [
    "naukri_fetcher.py",
    "naukri_adapter.py",
    "naukri_parser.py",
    "search_worker.py",
    "source_registry.py",
    "source_adapter.py",
]


def main():
    failures = []

    print("BROWSER RUNTIME PROBE INERTNESS TEST")
    print("======================================")

    probe_path = ROOT / "scripts" / PROBE_NAME
    if not probe_path.exists():
        failures.append(f"{PROBE_NAME} not found")
    else:
        print(f"PASS: {PROBE_NAME} exists")

    for filename in PRODUCTION_FILES:
        path = ROOT / "scripts" / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if PROBE_NAME in text:
            failures.append(f"{filename} references {PROBE_NAME} -- the diagnostic probe must never be wired into the production path")
        else:
            print(f"PASS: {filename} does not reference {PROBE_NAME}")

    # Confirm it makes no reference to the real Naukri domain -- it must
    # only ever target a local file:// fixture.
    probe_text = probe_path.read_text(encoding="utf-8")
    if "naukri.com" in probe_text.lower():
        failures.append(f"{PROBE_NAME} references naukri.com -- it must remain a purely local, offline diagnostic")
    else:
        print(f"PASS: {PROBE_NAME} contains no reference to naukri.com (local file:// fixture only)")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll browser-runtime-probe inertness tests passed.")


if __name__ == "__main__":
    main()
