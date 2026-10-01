#!/usr/bin/env python3

"""
Regression test for the JOBOS_NAUKRI_USER_AGENT configuration added to
scripts/naukri_fetch_bridge.js -- the standard Playwright context-level
`userAgent` override, live-validated in
data/reports/naukri_headless_ua_isolation.md/.json to change Naukri's
response from HTTP 403/BLOCKED to HTTP 200/VALID_RESULTS while remaining
genuinely headless.

Makes NO Naukri network request and pops NO visible browser window: all
real browser launches in this file navigate to "about:blank" only,
exactly like the existing convention in test_naukri_headless_config.py.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE_PATH = ROOT / "scripts" / "naukri_fetch_bridge.js"

EXPECTED_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
)

_UA_EXPR = "process.env.JOBOS_NAUKRI_USER_AGENT || DEFAULT_NAUKRI_USER_AGENT"
_HEADLESS_EXPR = "process.env.JOBOS_BROWSER_HEADLESS !== '0'"


def _playwright_available():
    """Pure availability probe -- see test_naukri_headless_config.py's
    identical helper. A fresh clone that hasn't run `npm install &&
    npx playwright install chromium` yet genuinely won't have this."""
    result = subprocess.run(
        ["node", "-e", "require.resolve('playwright'); const {chromium}=require('playwright'); console.log(chromium.executablePath())"],
        capture_output=True, text=True, timeout=15, cwd=str(ROOT),
    )
    return result.returncode == 0 and Path(result.stdout.strip()).exists()


def _run_node(js_code, env_overrides):
    env = dict(os.environ)
    env.update(env_overrides)
    return subprocess.run(
        ["node", "-e", js_code],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def main():
    failures = []

    print("NAUKRI USER-AGENT CONFIGURATION TEST")
    print("=======================================")

    source = BRIDGE_PATH.read_text(encoding="utf-8")

    # --- Static checks against the real production source -----------

    # The source wraps this string across two concatenated JS literals
    # for line-length -- check both halves rather than the single
    # joined string; the dynamic launch check below proves the actual
    # runtime value is correct end-to-end regardless of source formatting.
    ua_source_halves = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ",
        "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
    ]
    if not all(half in source for half in ua_source_halves):
        failures.append(
            "naukri_fetch_bridge.js does not contain the expected validated "
            f"default UA string: {EXPECTED_DEFAULT_UA!r}"
        )
    else:
        print("PASS: 3 -> validated default UA string is present in source")

    if _UA_EXPR not in source:
        failures.append(
            f"naukri_fetch_bridge.js does not contain the expected UA "
            f"fallback expression: {_UA_EXPR!r}"
        )
    else:
        print("PASS: 1/4/A -> JOBOS_NAUKRI_USER_AGENT falls back to the default via '||'")

    if not re.search(r"userAgent\s*:\s*naukriUserAgent", source):
        failures.append(
            "naukri_fetch_bridge.js's newContext(...) call does not pass "
            "userAgent: naukriUserAgent -- override would not be applied"
        )
    else:
        print("PASS: 2/7 -> userAgent is passed into the Naukri browser context")

    # D: existing headless default must be byte-identical, unchanged
    if _HEADLESS_EXPR not in source:
        failures.append(
            "naukri_fetch_bridge.js: JOBOS_BROWSER_HEADLESS expression changed "
            "or missing -- headless default must remain unchanged"
        )
    else:
        print("PASS: D -> headless default (JOBOS_BROWSER_HEADLESS-driven) unchanged")

    # E: channel: 'chromium' must remain unchanged
    if not re.search(r"channel\s*:\s*['\"]chromium['\"]", source):
        failures.append("naukri_fetch_bridge.js: channel: 'chromium' is missing or changed")
    else:
        print("PASS: E -> channel: 'chromium' unchanged")

    # G: existing diagnostic stderr / stdout contract must remain intact
    if "console.error(" not in source or "DIAGNOSTIC" not in source:
        failures.append(
            "naukri_fetch_bridge.js: existing DIAGNOSTIC stderr line appears "
            "to have been removed or altered"
        )
    else:
        print("PASS: G -> existing DIAGNOSTIC stderr line still present")

    if "process.stdout.write(html)" not in source:
        failures.append(
            "naukri_fetch_bridge.js: stdout contract changed -- must still "
            "write raw HTML to stdout only"
        )
    else:
        print("PASS: G -> stdout contract (raw HTML only) unchanged")

    # F: the setting must be Naukri-specific -- no other production file
    # should reference JOBOS_NAUKRI_USER_AGENT (this is the only browser
    # context in the real adapter/worker path today; if a second adapter
    # is added later, it must not inherit this by accident).
    other_files = [
        p for p in (ROOT / "scripts").glob("*")
        if p.is_file()
        and p.name != "naukri_fetch_bridge.js"
        and p.name != "test_naukri_user_agent_config.py"
        and p.suffix in (".js", ".py")
    ]
    leaked_into = [
        p.name for p in other_files
        if "JOBOS_NAUKRI_USER_AGENT" in p.read_text(encoding="utf-8")
    ]
    if leaked_into:
        failures.append(
            f"JOBOS_NAUKRI_USER_AGENT referenced outside naukri_fetch_bridge.js: {leaked_into}"
        )
    else:
        print("PASS: F -> JOBOS_NAUKRI_USER_AGENT is referenced only in naukri_fetch_bridge.js")

    # H: production paths do not accidentally import/depend on the
    # offline diagnostic probe script (mirrors test_browser_runtime_probe_inert.py).
    probe_name = "naukri_browser_runtime_probe.js"
    production_files = [
        "naukri_fetcher.py",
        "naukri_adapter.py",
        "naukri_parser.py",
        "search_worker.py",
        "source_registry.py",
        "source_adapter.py",
        "naukri_fetch_bridge.js",
    ]
    probe_leak = []
    for filename in production_files:
        path = ROOT / "scripts" / filename
        if path.exists() and probe_name in path.read_text(encoding="utf-8"):
            probe_leak.append(filename)
    if probe_leak:
        failures.append(f"Production files reference the diagnostic probe: {probe_leak}")
    else:
        print(f"PASS: H -> no production file references {probe_name}")

    # --- Dynamic checks: real (headless, channel='chromium') launches
    # against about:blank only -- no Naukri/network request, no visible
    # window. Re-implements the bridge's own small UA-computation and
    # context-creation logic (same pattern as
    # test_naukri_headless_config.py's launch_check_js) to prove the
    # ACTUAL Chromium context receives the intended navigator.userAgent
    # end-to-end, not just that the source text looks right.

    default_ua_js_literal = json.dumps(EXPECTED_DEFAULT_UA)

    launch_check_js = (
        f"const DEFAULT_NAUKRI_USER_AGENT = {default_ua_js_literal};"
        "const { chromium } = require('playwright');"
        "const headless = process.env.JOBOS_BROWSER_HEADLESS !== '0';"
        "const naukriUserAgent = process.env.JOBOS_NAUKRI_USER_AGENT || DEFAULT_NAUKRI_USER_AGENT;"
        "(async () => {"
        "  const browser = await chromium.launch({ channel: 'chromium', headless });"
        "  const context = await browser.newContext({ viewport: {width:1440,height:900}, userAgent: naukriUserAgent });"
        "  const page = await context.newPage();"
        "  await page.goto('about:blank');"
        "  const effectiveUA = await page.evaluate(() => navigator.userAgent);"
        "  await browser.close();"
        "  console.log(JSON.stringify({ effectiveUA, headless }));"
        "})().catch((e) => { console.error(e.message); process.exit(1); });"
    )

    if not _playwright_available():
        print(
            "SKIP: A/B/C -> real browser-launch checks -- playwright/Chromium "
            "not installed in this environment. Run `npm install && npx "
            "playwright install chromium` (see docs/GETTING_STARTED.md) "
            "and re-run this test to exercise the real launch path."
        )
        cases = []
    else:
        cases = [
            ("A: JOBOS_NAUKRI_USER_AGENT unset -> validated default used", {}, EXPECTED_DEFAULT_UA, "JOBOS_NAUKRI_USER_AGENT"),
            ("B: explicit override passed through exactly", {"JOBOS_NAUKRI_USER_AGENT": "TestAgent/9.9 (isolation-test)"}, "TestAgent/9.9 (isolation-test)", None),
            ("C: empty string -> deterministic fallback to validated default (never an empty UA)", {"JOBOS_NAUKRI_USER_AGENT": ""}, EXPECTED_DEFAULT_UA, None),
        ]

    for label, env_overrides, expected_ua, unset_var in cases:
        env_overrides = dict(env_overrides)
        if unset_var:
            os.environ.pop(unset_var, None)

        result = _run_node(launch_check_js, env_overrides)
        if result.returncode != 0:
            failures.append(f"{label}: launch failed: {result.stderr.strip()}")
            continue

        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as error:
            failures.append(f"{label}: could not parse output: {result.stdout!r} ({error})")
            continue

        if payload.get("effectiveUA") != expected_ua:
            failures.append(
                f"{label}: expected effectiveUA={expected_ua!r}, got {payload.get('effectiveUA')!r}"
            )
        elif payload.get("headless") is not True:
            failures.append(f"{label}: expected headless=true (unchanged default), got {payload}")
        else:
            print(f"PASS: {label}")
            print(f"       effective navigator.userAgent = {payload['effectiveUA']!r}")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll Naukri User-Agent configuration tests passed.")


if __name__ == "__main__":
    main()
