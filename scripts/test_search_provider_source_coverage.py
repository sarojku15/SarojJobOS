#!/usr/bin/env python3

"""
Regression test for the root-cause bug this session found and fixed:
search_provider_adapter.py's SearchProviderAdapterBase.status (and the
per-instance status __init__ recomputes) is gated on
search_provider.get_configured_providers(), which reads *_API_KEY
values from os.environ -- but nothing loaded .env's real key values
into os.environ for any entrypoint EXCEPT api/main.py (which did so
explicitly, inline). scripts/submit_search.py and
scripts/run_search_worker.py -- the actual CLI path a normal/scheduled
search uses -- never did, so in THOSE processes every one of the 7
search-provider-backed sources (LINKEDIN_SEARCH, INDEED_SEARCH, etc.)
silently computed NOT_ENABLED even with real keys configured, and
search_profile._default_sources() (what an unconfigured "run a normal
search" call actually uses) silently fell back to the 4 direct sources
only.

Fix: search_provider.py itself now calls
env_config.load_env_file_into_environ() at ITS OWN module import time
-- the root-cause fix point, since every entrypoint that ever reaches
search_provider_adapter.py must first import search_provider.py.

Tests:
  A. provider configured -> all 7 restricted boards included in the
     normal search plan (_default_sources()), from a completely fresh
     subprocess that never manually loaded .env -- exactly reproducing
     scripts/submit_search.py's own real-world import path.
  B. provider not configured (all 5 provider keys absent) -> restricted
     boards are NOT_ENABLED / not falsely reported as live-searchable,
     and _default_sources() falls back to the 4 direct sources only.

Both run as REAL SUBPROCESSES (not in-process imports) specifically
because the bug was about what a FRESH PROCESS's os.environ looks like
at first import -- an in-process test would not reproduce it (this
process's own os.environ, and Python's module cache, would already be
contaminated by whatever imported search_provider.py first).

Never opens data/applications/jobos.db. Makes no network call (pure
env-var + class-attribute introspection).
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python3"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

_RESTRICTED_SEARCH_KEYS = [
    "LINKEDIN_SEARCH", "INDEED_SEARCH", "FOUNDIT_SEARCH", "INSTAHYRE_SEARCH",
    "CUTSHORT_SEARCH", "WELLFOUND_SEARCH", "SHINE_SEARCH",
]
_DIRECT_KEYS = ["NAUKRI", "HIRIST", "IIMJOBS", "APNA"]

_PROBE_SCRIPT = """
import sys, json
sys.path.insert(0, {scripts_dir!r})
from search_profile import _default_sources
import source_registry
result = {{
    "default_sources": sorted(_default_sources()),
    "search_provider_statuses": {{
        k: str(source_registry.get_adapter_status(k)) for k in {search_keys!r}
    }},
}}
print(json.dumps(result))
"""


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _run_probe(env_overrides):
    """Runs the probe script as a genuinely fresh subprocess with the
    given environment overrides layered onto a MINIMAL base env (PATH
    + HOME only, so the real project .env -- read from disk by the
    subprocess itself, exactly like a real entrypoint -- is the only
    source of any ambient provider key, not this test's own os.environ)."""
    import os

    script = _PROBE_SCRIPT.format(scripts_dir=str(SCRIPTS_DIR), search_keys=_RESTRICTED_SEARCH_KEYS)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    env.update(env_overrides)
    result = subprocess.run(
        [PYTHON, "-c", script], cwd=str(ROOT), env=env,
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"probe subprocess failed: {result.stdout}\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a_provider_configured_includes_all_7_restricted_boards():
    failures = []

    # This test specifically proves a REAL provider key on disk gets
    # picked up correctly by a fresh process -- it cannot fabricate
    # that condition without reintroducing the exact ambient-os.environ
    # contamination risk this file's own docstring explains. .env is
    # optional and gitignored (no provider key is required to run
    # JobOS), so a fresh clone genuinely may not have one configured;
    # skip cleanly rather than fail in that case.
    sys.path.insert(0, str(SCRIPTS_DIR))
    import search_provider as _search_provider
    _any_configured = any(
        _search_provider.is_provider_configured(name) for name in _search_provider.PROVIDER_ENV_KEYS
    )
    if not _any_configured:
        print(
            "SKIP: test A -> no search-provider *_API_KEY configured in "
            "config/jobos.env.example-derived .env on this machine; this "
            "is an optional integration, not required to run JobOS -- "
            "see docs/CONFIGURATION.md to configure one and re-run this test"
        )
        return failures

    # No env override needed beyond PATH/HOME -- the real project .env
    # on disk already has at least one provider key configured, and
    # THAT is exactly what this test proves gets picked up correctly
    # by a fresh process (reproducing scripts/submit_search.py).
    probe = _run_probe({})

    defaults = set(probe["default_sources"])
    missing_direct = set(_DIRECT_KEYS) - defaults
    missing_restricted = set(_RESTRICTED_SEARCH_KEYS) - defaults

    if missing_direct:
        _fail(failures, f"A: expected all 4 direct sources in _default_sources(), missing {missing_direct}")
    if missing_restricted:
        _fail(
            failures,
            f"A: SAFETY REGRESSION -- expected all 7 restricted boards in _default_sources() "
            f"when a provider is configured, missing {missing_restricted} (got {sorted(defaults)}) "
            f"-- this is exactly the root-cause bug this test protects against",
        )
    not_enabled = [k for k, v in probe["search_provider_statuses"].items() if "NOT_ENABLED" in v]
    if not_enabled:
        _fail(failures, f"A: expected every restricted board's registry status to be ENABLED with a provider configured, still NOT_ENABLED: {not_enabled}")

    if not failures:
        print(f"PASS: A -> a fresh process (reproducing scripts/submit_search.py's own import path) correctly includes all 11 sources: {sorted(defaults)}")

    return failures


def test_b_provider_not_configured_excludes_restricted_boards():
    failures = []

    # Explicitly blank every provider key -- simulates an environment
    # with genuinely no search-provider configured at all.
    blank_keys = {k: "" for k in ("YOU_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY", "SERPER_API_KEY")}
    probe = _run_probe(blank_keys)

    defaults = set(probe["default_sources"])
    missing_direct = set(_DIRECT_KEYS) - defaults
    present_restricted = set(_RESTRICTED_SEARCH_KEYS) & defaults

    if missing_direct:
        _fail(failures, f"B: expected all 4 direct sources still present with no provider configured, missing {missing_direct}")
    if present_restricted:
        _fail(
            failures,
            f"B: SAFETY VIOLATION -- restricted boards falsely reported as live-searchable with "
            f"NO provider configured: {present_restricted}",
        )
    still_enabled = [k for k, v in probe["search_provider_statuses"].items() if "NOT_ENABLED" not in v]
    if still_enabled:
        _fail(failures, f"B: expected every restricted board's registry status to be NOT_ENABLED with no provider configured, still enabled: {still_enabled}")

    if not failures:
        print(f"PASS: B -> with no provider configured, _default_sources() correctly falls back to the 4 direct sources only: {sorted(defaults)}")

    return failures


def main():
    failures = []
    print("SEARCH-PROVIDER SOURCE COVERAGE REGRESSION TEST (root-cause fix)")
    print("=================================================================================")

    for test in [
        test_a_provider_configured_includes_all_7_restricted_boards,
        test_b_provider_not_configured_excludes_restricted_boards,
    ]:
        failures.extend(test())

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)

    print("\nAll search-provider source coverage tests passed.")


if __name__ == "__main__":
    main()
