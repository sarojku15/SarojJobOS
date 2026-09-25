// Minimal Python -> Node/Playwright fetch bridge for Hirist. Mirrors
// scripts/naukri_fetch_bridge.js's exact, already-validated
// configuration -- channel: 'chromium', headless driven by
// JOBOS_BROWSER_HEADLESS, no stealth/UA/fingerprint customization of
// any kind. Built for Phase 6 Step 4's single, explicitly-authorized
// live validation query; kept as narrowly scoped as Naukri's own
// bridge.
//
// Usage: node scripts/hirist_fetch_bridge.js <url>
//
// Headless mode: controlled by JOBOS_BROWSER_HEADLESS, identical
// convention to naukri_fetch_bridge.js -- default is headless (true)
// whenever the variable is unset or anything other than the literal
// string "0".
//
// No User-Agent override is applied. Unlike Naukri (where a headless
// User-Agent was live-validated to trigger a block), nothing in
// Hirist's Phase 6 inspection evidence indicated any headless-specific
// blocking behavior -- applying an unevidenced customization here
// would be exactly the kind of "invented behavior" this project's own
// strict-evidence discipline forbids.
//
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
  console.error('Usage: node hirist_fetch_bridge.js <url>');
  process.exit(1);
}

const headless = process.env.JOBOS_BROWSER_HEADLESS !== '0';

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
  });

  const page = await context.newPage();

  try {
    const response = await page.goto(url, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    });

    // Diagnostic only -- stderr, never stdout (stdout stays pure HTML).
    // Mirrors naukri_fetch_bridge.js's own diagnostic contract exactly.
    if (response) {
      console.error(
        'DIAGNOSTIC ' + JSON.stringify({
          requestedUrl: url,
          finalUrl: response.url(),
          status: response.status(),
        })
      );
    }

    await page.waitForTimeout(3000);

    const html = await page.content();

    process.stdout.write(html);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
