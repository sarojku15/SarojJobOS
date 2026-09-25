#!/usr/bin/env python3

"""
Thin wrapper around the Node/Playwright fetch bridge
(hirist_fetch_bridge.js). Mirrors scripts/naukri_fetcher.py's exact
contract: one subprocess invocation per URL, injectable so tests
substitute a fake fetcher instead of invoking Node/Playwright at all.

Live-capable by construction -- exactly like NaukriFetcher. Safety
against accidental production use is NOT this class's job: it is
provided entirely by HiristAdapter.status == AdapterStatus.NOT_ENABLED
(the registry-level pre-flight gate in source_registry.
discover_from_sources() skips a NOT_ENABLED adapter before
health_check()/search() -- and therefore this fetcher -- is ever
called), the same architecture Naukri itself relies on.
"""

import subprocess
import sys
from pathlib import Path

from source_adapter import AdapterTimeoutError


BRIDGE_SCRIPT = Path(__file__).resolve().parent / "hirist_fetch_bridge.js"

FETCH_TIMEOUT_SECONDS = 75


class HiristFetcher:
    def fetch(self, url: str) -> str:
        try:
            result = subprocess.run(
                ["node", str(BRIDGE_SCRIPT), url],
                capture_output=True,
                text=True,
                timeout=FETCH_TIMEOUT_SECONDS,
                shell=False,
            )
        except subprocess.TimeoutExpired as error:
            raise AdapterTimeoutError(
                "HIRIST",
                detail=(
                    f"fetch bridge timed out after "
                    f"{FETCH_TIMEOUT_SECONDS}s: {error}"
                ),
            ) from error

        if result.returncode != 0:
            raise AdapterTimeoutError(
                "HIRIST",
                detail=(
                    result.stderr.strip()
                    or f"fetch bridge exited with code {result.returncode}"
                ),
            )

        if not result.stdout:
            raise AdapterTimeoutError(
                "HIRIST",
                detail="fetch bridge returned empty stdout",
            )

        if result.stderr:
            print(result.stderr, file=sys.stderr)

        return result.stdout
