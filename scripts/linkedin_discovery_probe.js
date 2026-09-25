// LinkedIn Phase 5 discovery/feasibility probe -- EXPLORATORY, NOT a
// production adapter (mirrors this project's existing convention: see
// naukri_discovery_probe.js / naukri_search_probe.js, documented in
// CLAUDE.md under "Naukri Playwright Probes (exploratory -- not
// production adapters)"). Never imported or invoked by
// linkedin_adapter.py, source_registry.py, or any production path.
//
// Purpose: ONE controlled, read-only visit to ONE public LinkedIn Jobs
// search URL, to observe -- not assume -- whether it is reachable
// without authentication, and what its actual structure/behavior is.
//
// Usage: node scripts/linkedin_discovery_probe.js
//
// Safety, matching this task's explicit constraints:
//   - Exactly ONE page navigation. No retries, no pagination, no
//     scrolling-triggered "load more" interaction, no clicks.
//   - No login, no credentials, no cookies/session reuse, no stored
//     LinkedIn account of any kind.
//   - No CAPTCHA solving, no anti-bot workaround, no header/UA
//     spoofing beyond the project's existing, non-evasive
//     channel='chromium' + JOBOS_BROWSER_HEADLESS convention (the same
//     configuration already used for Naukri, kept identical here on
//     purpose so this observation is not biased by a LinkedIn-specific
//     customization).
//   - Headless by default (JOBOS_BROWSER_HEADLESS convention), so no
//     visible browser window pops during this inspection, consistent
//     with the project-wide background-only requirement.
//   - Only observes and reports; makes no judgment call to retry,
//     escalate, or route around whatever LinkedIn actually returns.

const { chromium } = require('playwright');

const headless = process.env.JOBOS_BROWSER_HEADLESS !== '0';

// One reasonable, standard, publicly-documented LinkedIn Jobs search
// URL pattern (the same URL shape any logged-out visitor typing into
// their own browser would land on) -- NOT verified against a prior
// live capture, since none exists yet for LinkedIn. This IS the live
// test: whatever this URL actually returns is the evidence, not an
// assumption. No LinkedIn API, guest endpoint, or non-public URL was
// used or invented.
const INSPECTION_URL =
  'https://www.linkedin.com/jobs/search?keywords=Site%20Reliability%20Engineer&location=Bengaluru%2C%20Karnataka%2C%20India';

// Diagnostic-only marker phrases -- an ad hoc classification scheme
// built fresh for THIS inspection task, not a reused or modified copy
// of naukri_parser.py's classifier (LinkedIn's page structure is
// unrelated to Naukri's). Every phrase below is checked as a plain
// case-insensitive substring of the page's own rendered body text or
// URL; nothing here is a guess at a hidden API or selector.
const MARKERS = {
  loginWallUrl: (url) =>
    url.includes('/authwall') || url.includes('/login') || url.includes('/uas/login'),
  loginWallText: (text) =>
    text.includes('sign in') || text.includes('join now') || text.includes('welcome back'),
  captchaOrChallenge: (text) =>
    text.includes('captcha') ||
    text.includes('recaptcha') ||
    text.includes('security check') ||
    text.includes('checkpoint') ||
    text.includes('unusual activity') ||
    text.includes('verify you are a human'),
  accessDenied: (text) =>
    text.includes('access denied') || text.includes('request blocked') || text.includes('403 forbidden'),
  emptyResults: (text) =>
    text.includes('no matching jobs') || text.includes('0 results') || text.includes('no results found'),
};

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

  const result = {
    inspectionUrl: INSPECTION_URL,
    headless,
    channel: 'chromium',
  };

  try {
    const response = await page.goto(INSPECTION_URL, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    });

    result.httpStatus = response ? response.status() : null;
    result.finalUrl = page.url();
    result.redirected = result.finalUrl !== INSPECTION_URL;

    if (response) {
      console.error(
        'DIAGNOSTIC ' +
          JSON.stringify({
            requestedUrl: INSPECTION_URL,
            finalUrl: result.finalUrl,
            status: result.httpStatus,
          })
      );
    }

    await page.waitForTimeout(4000);

    result.title = await page.title();

    const effectiveUserAgent = await page.evaluate(() => navigator.userAgent);
    result.effectiveUserAgent = effectiveUserAgent;

    const bodyText = (await page.locator('body').innerText().catch(() => '')).toLowerCase();
    result.bodyTextLength = bodyText.length;

    result.markers = {
      loginWallUrl: MARKERS.loginWallUrl(result.finalUrl),
      loginWallText: MARKERS.loginWallText(bodyText),
      captchaOrChallenge: MARKERS.captchaOrChallenge(bodyText),
      accessDenied: MARKERS.accessDenied(bodyText),
      emptyResults: MARKERS.emptyResults(bodyText),
    };

    // Diagnostic-only structural probes -- checked for PRESENCE ONLY
    // (count of matching elements), never relied upon as a confirmed,
    // production-ready selector. A count of 0 is reported honestly as
    // "not found", never silently treated as "must be a different
    // selector, try again."
    const structuralProbe = await page.evaluate(() => {
      function countAll(selector) {
        try {
          return document.querySelectorAll(selector).length;
        } catch (e) {
          return -1;
        }
      }
      return {
        anchorsToJobsView: countAll('a[href*="/jobs/view/"]'),
        elementsWithJobCardClassHint: countAll('[class*="job-card" i], [class*="jobs-search" i]'),
        formsPresent: countAll('form'),
        passwordInputsPresent: countAll('input[type="password"]'),
        iframesPresent: countAll('iframe'),
      };
    });
    result.structuralProbe = structuralProbe;

    // Bounded evidence excerpt only -- not the full page, and never
    // written into the project repo (the calling shell redirects
    // stdout to a scratchpad file, not a tracked path).
    const html = await page.content();
    result.htmlLength = html.length;
    result.htmlExcerptFirst4000Chars = html.slice(0, 4000);

    result.navigationError = null;
  } catch (error) {
    result.navigationError = error.message;
  } finally {
    await browser.close();
  }

  console.log(JSON.stringify(result, null, 2));
})();
