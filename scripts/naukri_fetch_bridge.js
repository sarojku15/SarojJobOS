// Minimal Python -> Node/Playwright fetch bridge for Naukri.
//
// Usage: node scripts/naukri_fetch_bridge.js <url>
//
// Reuses the exact browser configuration already proven safe this
// session (1440x900 viewport, domcontentloaded + settle wait) -- the
// same setup used successfully by naukri_discovery_probe.js and
// naukri_search_probe.js.
//
// Headless mode: controlled by JOBOS_BROWSER_HEADLESS so automated/
// background runs (the search worker, CI, this project's live
// validation scripts) never pop a visible browser window. Default is
// headless (true) whenever the variable is unset or anything other
// than the literal string "0". Set JOBOS_BROWSER_HEADLESS=0 only for
// interactive debugging.
//
// Historical note: an early exploratory attempt (naukri_extractor.js,
// pre-dating this bridge) was blocked in headless mode and succeeded
// once switched to headed -- which is why this file originally
// launched the browser in headed mode unconditionally. If headless
// mode is ever found to trigger
// a genuine new block/challenge from Naukri, that is a real,
// reproducible finding to investigate (see naukri_parser.py's
// SearchPageState classifier), not a reason to silently revert this
// default -- use JOBOS_BROWSER_HEADLESS=0 to fall back to headed mode
// in the meantime.
//
// channel: 'chromium' -- diagnosed and fixed in the same session that
// first enabled headless-by-default: Playwright's chromium.launch()
// with no explicit channel silently substitutes a separate, dedicated
// "headless shell" binary (chromium_headless_shell-<rev>) whenever
// headless is true, instead of running the SAME full Chromium binary
// (chromium-<rev>) used by every previously-successful Naukri access
// in this project (every probe script, and this bridge before this
// change -- all of them headed, which can only use the full binary).
// Pinning channel: 'chromium' forces the full binary in BOTH headless
// and headed mode, so the only variable that changes between this
// bridge and every prior successful run is "hidden window vs. visible
// window" -- never the browser fingerprint itself. See
// data/reports/naukri_headless_block_diagnosis.md for the full
// evidence trail (installed-binary inspection + the actual blocked
// process's command line from the one headless-shell run attempted).
// Prints the fetched page's full HTML to stdout ONLY. All diagnostic
// and error output goes to stderr, so a Python caller can capture
// stdout directly as the page content via subprocess.
//
// Does not click, submit, log in, solve CAPTCHAs, rotate proxies,
// change fingerprints, use cookie/session tricks, retry, or perform
// any other interaction with the page. Exits non-zero on any
// navigation or fetch failure.

const { chromium } = require('playwright');

const url = process.argv[2];

if (!url) {
  console.error('Usage: node naukri_fetch_bridge.js <url>');
  process.exit(1);
}

const headless = process.env.JOBOS_BROWSER_HEADLESS !== '0';

// Naukri-specific User-Agent override. Evidence trail:
// data/reports/naukri_browser_runtime_diagnostic.md/.json (offline
// measurement: native headless Chromium's default User-Agent contains
// the literal substring "HeadlessChrome", headed mode's does not -- the
// only FACT-level runtime difference found between otherwise-identical
// headed/headless channel='chromium' launches) and
// data/reports/naukri_headless_ua_isolation.md/.json (the single live
// experiment: native headless + this exact UA string -> HTTP 200 /
// VALID_RESULTS / 20 jobs, matching a genuinely headed run exactly,
// versus native headless + the default HeadlessChrome UA -> HTTP 403 /
// BLOCKED / 0 jobs). Uses ONLY Playwright's standard, documented
// context-level `userAgent` option -- no navigator/WebGL/pointer
// property is touched by JS, no header beyond User-Agent is altered,
// no stealth or fingerprint-spoofing library is used.
//
// JOBOS_NAUKRI_USER_AGENT lets this be overridden without a code
// change. Unset -> the validated default below. Explicitly set to an
// empty string -> also falls back to the validated default (`||`
// treats an empty string as falsy) rather than passing an empty/
// malformed User-Agent to Playwright -- see
// scripts/test_naukri_user_agent_config.py for the regression coverage
// of every one of these cases. This setting is read only here, in the
// Naukri-specific bridge -- it has no effect on any other adapter.
const DEFAULT_NAUKRI_USER_AGENT =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36';

const naukriUserAgent = process.env.JOBOS_NAUKRI_USER_AGENT || DEFAULT_NAUKRI_USER_AGENT;

(async () => {
  const browser = await chromium.launch({
    channel: 'chromium',
    headless,
  });

  const context = await browser.newContext({
    viewport: {
      width: 1440,
      height: 900,
    },
    userAgent: naukriUserAgent,
  });

  const page = await context.newPage();

  try {
    const response = await page.goto(url, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    });

    // Diagnostic only -- stderr, never stdout (stdout stays pure HTML,
    // per this file's own existing contract). Never inspected to make
    // any navigation/retry/classification decision here; that remains
    // naukri_parser.py's job on the returned HTML alone. Added so a
    // human or a live-test driver capturing this process's stderr can
    // see the actual HTTP status and final (post-redirect) URL for a
    // request, which nothing in this pipeline previously recorded.
    if (response) {
      console.error(
        'DIAGNOSTIC ' + JSON.stringify({
          requestedUrl: url,
          finalUrl: response.url(),
          status: response.status(),
        })
      );
    }

    await page.waitForTimeout(6000);

    const html = await page.content();

    process.stdout.write(html);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
