const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const DATA_DIR = path.join(ROOT, 'data');
const FIXTURES_DIR = path.join(ROOT, 'data', 'fixtures', 'naukri');

fs.mkdirSync(DATA_DIR, { recursive: true });
fs.mkdirSync(FIXTURES_DIR, { recursive: true });

(async () => {
  const browser = await chromium.launch({
    headless: false,
  });

  const context = await browser.newContext({
    viewport: {
      width: 1440,
      height: 900,
    },
  });

  const page = await context.newPage();

  const searchUrl =
    'https://www.naukri.com/senior-site-reliability-engineer-jobs-in-bengaluru';

  console.log('NAUKRI SEARCH PROBE');
  console.log('===================');
  console.log(`Search URL: ${searchUrl}`);
  console.log();

  try {
    await page.goto(searchUrl, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    });

    await page.waitForTimeout(7000);

    const title = await page.title();
    const url = page.url();

    const bodyText = (
      await page.locator('body').innerText()
    ).toLowerCase();

    const links = await page.locator('a').count();

    const textLength = bodyText.length;

    const jobSignals = [
      'job',
      'jobs',
      'experience',
      'salary',
      'location',
      'apply',
    ];

    const signalResults = {};

    for (const signal of jobSignals) {
      signalResults[signal] =
        bodyText.includes(signal);
    }

    const screenshotPath = path.join(
      DATA_DIR,
      'naukri_search_probe.png'
    );

    await page.screenshot({
      path: screenshotPath,
      fullPage: true,
    });

    const html = await page.content();

    const fixturePath = path.join(
      FIXTURES_DIR,
      'search_results_sre_bengaluru.html'
    );

    fs.writeFileSync(fixturePath, html, 'utf-8');

    console.log('RESULT');
    console.log('======');

    console.log(`Final URL       : ${url}`);
    console.log(`Title           : ${title}`);
    console.log(`Links           : ${links}`);
    console.log(`Body text chars : ${textLength}`);

    console.log();
    console.log('Job-page signals:');

    for (const [signal, found] of Object.entries(
      signalResults
    )) {
      console.log(
        `${signal.padEnd(15)}: ${found}`
      );
    }

    console.log();
    console.log(
      `Screenshot      : ${screenshotPath}`
    );
    console.log(
      `Fixture         : ${fixturePath}`
    );

    console.log();
    console.log(
      'Browser remains open for 15 seconds for inspection.'
    );

  } catch (error) {
    console.log();
    console.log('ERROR');
    console.log('=====');
    console.log(error.message);
  }

  await page.waitForTimeout(15000);
  await browser.close();

  console.log();
  console.log('Search probe complete.');
})();
