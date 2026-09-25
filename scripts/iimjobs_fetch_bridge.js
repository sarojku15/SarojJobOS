// Minimal Python -> Node/Playwright fetch bridge for iimjobs (Phase 11).
// Byte-for-byte the same generic fetch logic as naukri_fetch_bridge.js
// and hirist_fetch_bridge.js -- channel: 'chromium', headless driven by
// JOBOS_BROWSER_HEADLESS, no stealth/UA/fingerprint customization, no
// interaction with the page beyond navigation. iimjobs shares the same
// underlying platform as Hirist (identical robots.txt, identical
// schema.org ItemList/JobPosting JSON-LD shape, same S3 asset bucket --
// confirmed via a controlled live inspection this phase, see
// data/reports/phase11_public_multisource_completion.md), so no
// site-specific fetch behavior is needed here either.
//
// Usage: node scripts/iimjobs_fetch_bridge.js <url>
//
// Prints the fetched page's full HTML to stdout ONLY. All diagnostic
// and error output goes to stderr. Does not click, submit, log in,
// solve CAPTCHAs, rotate proxies, change fingerprints, or retry.
// Exits non-zero on any navigation or fetch failure.

const { chromium } = require('playwright');

const url = process.argv[2];

if (!url) {
  console.error('Usage: node iimjobs_fetch_bridge.js <url>');
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
