// Phase 12 investigation-only tool: loads a URL with Playwright and
// records every network request/response the PAGE ITSELF makes
// (XHR/fetch), so a legitimate public JSON API backing a
// client-rendered page can be discovered without guessing. Does not
// interact with the page beyond navigation+scroll -- no clicks, no
// form submission, no login, no CAPTCHA handling. Prints one JSON
// object to stdout: {finalUrl, status, requests: [{url, method,
// resourceType, status, contentType, bodySnippet}]}.
//
// Usage: node scripts/network_capture_probe.js <url> [scrollCount]

const { chromium } = require('playwright');

const url = process.argv[2];
const scrollCount = parseInt(process.argv[3] || '2', 10);

if (!url) {
  console.error('Usage: node network_capture_probe.js <url> [scrollCount]');
  process.exit(1);
}

const headless = process.env.JOBOS_BROWSER_HEADLESS !== '0';

(async () => {
  const browser = await chromium.launch({ channel: 'chromium', headless });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  const requests = [];

  page.on('response', async (response) => {
    const req = response.request();
    const resourceType = req.resourceType();
    if (resourceType !== 'xhr' && resourceType !== 'fetch') return;
    let bodySnippet = '';
    try {
      const text = await response.text();
      bodySnippet = text.slice(0, 800);
    } catch (e) {
      bodySnippet = `[unreadable: ${e.message}]`;
    }
    requests.push({
      url: req.url(),
      method: req.method(),
      resourceType,
      status: response.status(),
      contentType: response.headers()['content-type'] || '',
      bodySnippet,
    });
  });

  let finalUrl = url;
  let status = null;
  try {
    const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    if (resp) {
      finalUrl = resp.url();
      status = resp.status();
    }
    await page.waitForTimeout(3000);
    for (let i = 0; i < scrollCount; i++) {
      await page.mouse.wheel(0, 2000);
      await page.waitForTimeout(1500);
    }
  } catch (error) {
    console.error('NAVIGATION_ERROR: ' + error.message);
  } finally {
    console.log(JSON.stringify({ finalUrl, status, requests }, null, 2));
    await browser.close();
  }
})();
