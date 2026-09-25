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

  console.log('NAUKRI DISCOVERY PROBE');
  console.log('======================');
  console.log('Opening Naukri...');

  try {
    await page.goto('https://www.naukri.com/', {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    });

    await page.waitForTimeout(5000);

    const title = await page.title();
    const url = page.url();

    const bodyText = (
      await page.locator('body').innerText()
    ).toLowerCase();

    const indicators = {
      login:
        bodyText.includes('login') ||
        bodyText.includes('sign in'),

      captcha:
        bodyText.includes('captcha') ||
        bodyText.includes('recaptcha'),

      verification:
        bodyText.includes('verify') ||
        bodyText.includes('verification'),

      accessBlocked:
        bodyText.includes('access denied') ||
        bodyText.includes('request blocked') ||
        bodyText.includes('unusual traffic'),
    };

    const screenshotPath = path.join(
      DATA_DIR,
      'naukri_discovery_probe.png'
    );

    await page.screenshot({
      path: screenshotPath,
      fullPage: true,
    });

    const html = await page.content();

    const fixturePath = path.join(
      FIXTURES_DIR,
      'homepage.html'
    );

    fs.writeFileSync(fixturePath, html, 'utf-8');

    console.log();
    console.log('RESULT');
    console.log('======');
    console.log(`Final URL : ${url}`);
    console.log(`Title     : ${title}`);
    console.log();
    console.log('Page indicators:');
    console.log(`Login       : ${indicators.login}`);
    console.log(`CAPTCHA     : ${indicators.captcha}`);
    console.log(`Verification: ${indicators.verification}`);
    console.log(`Blocked     : ${indicators.accessBlocked}`);
    console.log();
    console.log(
      `Screenshot : ${screenshotPath}`
    );
    console.log(
      `Fixture    : ${fixturePath}`
    );

  } catch (error) {
    console.log();
    console.log('ERROR');
    console.log('=====');
    console.log(error.message);
  }

  console.log();
  console.log(
    'The browser will remain open for 15 seconds for inspection.'
  );

  await page.waitForTimeout(15000);

  await browser.close();

  console.log('Probe complete.');
})();
