#!/usr/bin/env python3

import subprocess
import sys
from pathlib import Path

from source_adapter import AdapterTimeoutError


BRIDGE_SCRIPT = Path(__file__).resolve().parent / "naukri_fetch_bridge.js"

FETCH_TIMEOUT_SECONDS = 75


class NaukriFetcher:
    """
    Thin wrapper around the Node/Playwright fetch bridge
    (naukri_fetch_bridge.js). One subprocess invocation per URL --
    simple, isolated, and easy to rate-limit from the caller between
    calls.

    Kept deliberately minimal and injectable: NaukriAdapter accepts a
    fetcher in its constructor, so tests can substitute a FakeFetcher
    that returns canned fixture HTML instead of invoking Node/
    Playwright at all.
    """

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
                "NAUKRI",
                detail=(
                    f"fetch bridge timed out after "
                    f"{FETCH_TIMEOUT_SECONDS}s: {error}"
                ),
            ) from error

        if result.returncode != 0:
            raise AdapterTimeoutError(
                "NAUKRI",
                detail=(
                    result.stderr.strip()
                    or f"fetch bridge exited with code {result.returncode}"
                ),
            )

        if not result.stdout:
            raise AdapterTimeoutError(
                "NAUKRI",
                detail="fetch bridge returned empty stdout",
            )

        # Previously discarded on success: the bridge script's own
        # diagnostic lines (status code, final/post-redirect URL --
        # see naukri_fetch_bridge.js) were only ever surfaced when the
        # bridge FAILED (folded into the AdapterTimeoutError above).
        # Forwarding them here too, on the success path, costs nothing
        # behaviorally (this function's return value and exceptions
        # are unchanged) but makes that evidence visible in whatever
        # already captures this process's own stderr.
        if result.stderr:
            print(result.stderr, file=sys.stderr)

        return result.stdout
