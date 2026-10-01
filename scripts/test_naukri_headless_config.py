#!/usr/bin/env python3

"""
Regression test for the JOBOS_BROWSER_HEADLESS configuration added to
scripts/naukri_fetch_bridge.js (the only Playwright launch point in the
actual search-worker/adapter path -- the naukri_*_probe.js scripts are
documented exploratory tools, not part of any automated pipeline, and
are intentionally untouched).

Makes NO Naukri network request and pops NO visible browser window at
any point, even for the "headed" (JOBOS_BROWSER_HEADLESS=0) case: that
case is verified by checking the computed boolean only, never by
actually launching a headed browser instance during an automated test
run (which would defeat the point of this being a background-safe
test suite).
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE_PATH = ROOT / "scripts" / "naukri_fetch_bridge.js"

_HEADLESS_EXPR = "process.env.JOBOS_BROWSER_HEADLESS !== '0'"


def _playwright_available():
    """Pure availability probe -- does not launch a browser, just
    checks whether the playwright Node module and its Chromium
    binary are installed (npm install && npx playwright install
    chromium, per docs/GETTING_STARTED.md). A fresh clone that hasn't
    run that setup step yet genuinely won't have it."""
    result = subprocess.run(
        ["node", "-e", "require.resolve('playwright'); const {chromium}=require('playwright'); console.log(chromium.executablePath())"],
        capture_output=True, text=True, timeout=15, cwd=str(ROOT),
    )
    return result.returncode == 0 and Path(result.stdout.strip()).exists()


def _run_node(js_code, env_overrides):
    import os
    env = dict(os.environ)
    env.update(env_overrides)
    result = subprocess.run(
        ["node", "-e", js_code],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    return result


def main():
    failures = []

    print("NAUKRI HEADLESS CONFIGURATION TEST")
    print("====================================")

    # --- static check: the hardcoded headless:false literal is gone,
    #     the env-driven expression is present, and channel: 'chromium'
    #     is pinned (forces the full Chromium binary, not the separate
    #     headless-shell binary Playwright substitutes by default) ---
    source = BRIDGE_PATH.read_text(encoding="utf-8")

    if re.search(r"headless\s*:\s*false", source):
        failures.append("naukri_fetch_bridge.js still contains a hardcoded 'headless: false' literal")
    else:
        print("PASS: no hardcoded 'headless: false' literal remains")

    if _HEADLESS_EXPR not in source:
        failures.append(f"naukri_fetch_bridge.js does not contain the expected headless expression: {_HEADLESS_EXPR!r}")
    else:
        print("PASS: headless mode is driven by JOBOS_BROWSER_HEADLESS")

    if not re.search(r"channel\s*:\s*['\"]chromium['\"]", source):
        failures.append("naukri_fetch_bridge.js does not pin channel: 'chromium'")
    else:
        print("PASS: A -> channel: 'chromium' is present in the actual launch configuration")

    # --- dynamic check: default (unset) and explicit "1" both compute
    #     headless=True, and ACTUALLY launch a real (headless,
    #     channel='chromium') browser against about:blank -- no Naukri
    #     request, no visible window. While the browser is still open,
    #     inspect `ps aux` synchronously to distinguish the full
    #     chromium binary from the separate headless-shell binary. ---
    launch_check_js = (
        "const { chromium } = require('playwright');"
        "const { execSync } = require('child_process');"
        "const headless = process.env.JOBOS_BROWSER_HEADLESS !== '0';"
        "(async () => {"
        "  const browser = await chromium.launch({ channel: 'chromium', headless });"
        "  const page = await browser.newPage();"
        "  await page.goto('about:blank');"
        "  let lines = [];"
        "  try {"
        "    const ps = execSync('ps aux').toString();"
        "    lines = ps.split('\\n').filter(l => l.toLowerCase().includes('chromium') && !l.toLowerCase().includes('grep'));"
        "  } catch (e) {}"
        "  const hasHeadlessShell = lines.some(l => l.toLowerCase().includes('headless_shell'));"
        "  const hasFullChromium = lines.some(l => l.toLowerCase().includes('chromium') && !l.toLowerCase().includes('headless_shell'));"
        "  await browser.close();"
        "  console.log(JSON.stringify({ headless, launched: true, hasHeadlessShell, hasFullChromium, sampleLine: (lines[0] || '').slice(0, 200) }));"
        "})().catch((e) => { console.error(e.message); process.exit(1); });"
    )

    if not _playwright_available():
        print(
            "SKIP: B/D -> real browser-launch checks -- playwright/Chromium "
            "not installed in this environment. Run `npm install && npx "
            "playwright install chromium` (see docs/GETTING_STARTED.md) "
            "and re-run this test to exercise the real launch path."
        )
        launch_targets = []
    else:
        launch_targets = [
            ("default (JOBOS_BROWSER_HEADLESS unset)", {}, True),
            ("JOBOS_BROWSER_HEADLESS=1", {"JOBOS_BROWSER_HEADLESS": "1"}, True),
        ]

    for label, env_overrides, expected_headless in launch_targets:
        import os
        env_overrides = dict(env_overrides)
        if "JOBOS_BROWSER_HEADLESS" not in env_overrides:
            os.environ.pop("JOBOS_BROWSER_HEADLESS", None)

        result = _run_node(launch_check_js, env_overrides)
        if result.returncode != 0:
            failures.append(f"{label}: node/playwright launch failed: {result.stderr.strip()}")
            continue

        try:
            import json
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as error:
            failures.append(f"{label}: could not parse launch-check output: {result.stdout!r} ({error})")
            continue

        if payload.get("headless") is not expected_headless or not payload.get("launched"):
            failures.append(f"{label}: expected headless={expected_headless} and a successful launch, got {payload}")
        else:
            print(f"PASS: B/D -> {label} -> real launch succeeded with channel='chromium', headless={payload['headless']} (no visible window, no Naukri request)")
            print(f"       process evidence: hasFullChromium={payload.get('hasFullChromium')}, hasHeadlessShell={payload.get('hasHeadlessShell')}")
            if payload.get("sampleLine"):
                print(f"       sample process   : {payload['sampleLine']}")
            if payload.get("hasHeadlessShell"):
                failures.append(f"{label}: channel='chromium' did not prevent the headless-shell binary from being used: {payload}")

    # --- explicit opt-out (JOBOS_BROWSER_HEADLESS=0): verify the
    #     COMPUTED boolean only -- never actually launch a headed
    #     browser during this automated test run ---
    boolean_only_js = "console.log(process.env.JOBOS_BROWSER_HEADLESS !== '0');"
    result = _run_node(boolean_only_js, {"JOBOS_BROWSER_HEADLESS": "0"})
    if result.returncode != 0 or result.stdout.strip() != "false":
        failures.append(f"JOBOS_BROWSER_HEADLESS=0: expected computed headless=false, got stdout={result.stdout!r} stderr={result.stderr!r}")
    else:
        print("PASS: C -> JOBOS_BROWSER_HEADLESS=0 -> computed headless=false (escape hatch verified without popping a real headed window in this test run)")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll Naukri headless-configuration tests passed.")


if __name__ == "__main__":
    main()
