#!/usr/bin/env python3

"""
Thin wrapper around the Node/Playwright fetch bridge
(iimjobs_fetch_bridge.js). Byte-for-byte the same contract as
hirist_fetcher.py/naukri_fetcher.py: one subprocess invocation per URL,
injectable so tests substitute a fake fetcher instead of invoking
Node/Playwright at all.

Live-capable by construction. Safety against accidental production use
comes entirely from IimjobsAdapter.status -- see that module.
"""

import subprocess
import sys
from pathlib import Path

from source_adapter import AdapterTimeoutError


BRIDGE_SCRIPT = Path(__file__).resolve().parent / "iimjobs_fetch_bridge.js"

FETCH_TIMEOUT_SECONDS = 75


class IimjobsFetcher:
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
                "IIMJOBS",
                detail=f"fetch bridge timed out after {FETCH_TIMEOUT_SECONDS}s: {error}",
            ) from error
        except (OSError, UnicodeDecodeError) as error:
            # subprocess.run() itself (not the bridge script) can fail
            # to start/complete -- e.g. FileNotFoundError/PermissionError
            # if `node` isn't on PATH, or a UnicodeDecodeError decoding
            # stdout/stderr under text=True if the bridge ever writes
            # non-UTF-8 bytes. Neither is a TimeoutExpired, so both
            # previously escaped this function uncaught and could abort
            # the entire multi-source search batch instead of being
            # isolated to this one query.
            raise AdapterTimeoutError(
                "IIMJOBS",
                detail=f"fetch bridge process failed to run: {error}",
            ) from error

        if result.returncode != 0:
            raise AdapterTimeoutError(
                "IIMJOBS",
                detail=result.stderr.strip() or f"fetch bridge exited with code {result.returncode}",
            )

        if not result.stdout:
            raise AdapterTimeoutError("IIMJOBS", detail="fetch bridge returned empty stdout")

        if result.stderr:
            print(result.stderr, file=sys.stderr)

        return result.stdout
