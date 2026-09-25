// Hirist Phase 6 discovery/feasibility probe -- EXPLORATORY, NOT a
// production adapter. Mirrors this project's existing convention (see
// naukri_discovery_probe.js, naukri_search_probe.js, and
// linkedin_discovery_probe.js). Never imported or invoked by
// hirist_adapter.py, source_registry.py, or any production path.
//
// Purpose: ONE controlled, read-only visit to ONE public Hirist Jobs
// search URL, to observe -- not assume -- reachability and structure.
//
// Usage: node scripts/hirist_discovery_probe.js
//
// Safety, matching this task's explicit constraints:
//   - Exactly ONE page navigation. No retries, no pagination, no
//     detail-page visits, no clicks, no form submission, no login,
//     no account creation, no Apply interaction.
//   - No stealth plugins, no fingerprint spoofing, no proxy rotation,
//     no header/UA customization beyond this project's existing,
//     non-evasive channel='chromium' + JOBOS_BROWSER_HEADLESS
//     convention (identical to the Naukri and LinkedIn probes, so this
//     observation is not biased by a Hirist-specific customization).
//   - Headless by default, consistent with the project-wide
//     background-only requirement.
//   - Only observes and reports; if blocked/challenged, this script
//     makes no second attempt -- the caller (this task) is instructed
//     to run it exactly once.

const { chromium } = require('playwright');

const headless = process.env.JOBOS_BROWSER_HEADLESS !== '0';

// One reasonable, best-effort public Hirist Jobs search URL, built
// from Hirist's own well-known SEO-slug convention (role-in-path,
// analogous to Naukri's own already-verified "<role>-jobs-in-<location>"
// pattern) -- NOT verified against any prior capture, since none
// exists yet for Hirist. This IS the live test: whatever this URL
// actually returns (a real results page, a redirect to a different
// real URL, or an error) is the evidence, not an assumption. No
// Hirist API, internal endpoint, or non-public URL was used or
// invented.
const INSPECTION_URL =
  'https://www.hirist.tech/search/senior-devops-engineer-jobs?locations=Bengaluru';

// Diagnostic-only marker phrases -- an ad hoc classification scheme
// built fresh for THIS inspection task, mapped afterwards (by the
// calling report, not this script) onto this project's existing
// SearchPageState concepts (VALID_RESULTS / VALID_EMPTY_RESULT /
// BLOCKED / SOFT_BLOCK_OR_CHALLENGE / PARSE_FAILURE). Every phrase
// below is checked as a plain case-insensitive substring of the
// page's own rendered body text or URL; nothing here is a guess at a
// hidden API or selector.
const MARKERS = {
  loginWallUrl: (url) =>
    url.includes('/login') || url.includes('/signin') || url.includes('/register'),
  loginWallText: (text) =>
    text.includes('sign in') || text.includes('log in') || text.includes('register now'),
  captchaOrChallenge: (text) =>
    text.includes('captcha') ||
    text.includes('recaptcha') ||
    text.includes('security check') ||
    text.includes('checkpoint') ||
    text.includes('unusual activity') ||
    text.includes('verify you are a human') ||
    text.includes('are you a robot'),
  accessDenied: (text) =>
    text.includes('access denied') || text.includes('request blocked') || text.includes('403 forbidden'),
  emptyResults: (text) =>
    text.includes('no matching jobs') ||
    text.includes('no results found') ||
    text.includes('0 jobs found') ||
    text.includes('no jobs found'),
  notFound: (text) =>
    text.includes('page not found') || text.includes('404') || text.includes("doesn't exist"),
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
      notFound: MARKERS.notFound(bodyText),
    };

    // Diagnostic-only structural probes -- presence/count only, never
    // relied upon as a confirmed, production-ready selector.
    const structuralProbe = await page.evaluate(() => {
      function countAll(selector) {
        try {
          return document.querySelectorAll(selector).length;
        } catch (e) {
          return -1;
        }
      }
      return {
        anchorsToJobDetail: countAll('a[href*="/job-detail/"], a[href*="/jobs/"], a[href*="/j/"]'),
        elementsWithJobCardClassHint: countAll(
          '[class*="job-card" i], [class*="jobCard" i], [class*="job-listing" i], [class*="search-result" i]'
        ),
        forms: countAll('form'),
        passwordInputs: countAll('input[type="password"]'),
        iframes: countAll('iframe'),
        paginationHints: countAll('[class*="pagination" i], nav[aria-label*="page" i]'),
      };
    });
    result.structuralProbe = structuralProbe;

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
