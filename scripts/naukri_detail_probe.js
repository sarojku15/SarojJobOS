const { chromium } = require('playwright');

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

  const jobUrl =
    'https://www.naukri.com/job-listings-site-reliability-engineer-acesoft-labs-bengaluru-5-to-10-years-180926917392';

  console.log('NAUKRI JOB DETAIL PROBE');
  console.log('=======================');
  console.log(`URL: ${jobUrl}`);

  try {
    await page.goto(jobUrl, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    });

    await page.waitForTimeout(6000);

    const title = await page.title();
    const url = page.url();

    const bodyText = await page.locator('body').innerText();

    console.log();
    console.log('PAGE');
    console.log('====');

    console.log(`Final URL : ${url}`);
    console.log(`Title     : ${title}`);
    console.log(
      `Body chars: ${bodyText.length}`
    );

    console.log();
    console.log('VISIBLE PAGE TEXT — FIRST 6000 CHARACTERS');
    console.log('==========================================');

    console.log(
      bodyText
        .replace(/\n{3,}/g, '\n\n')
        .slice(0, 6000)
    );

    console.log();
    console.log('LINKS / BUTTONS');
    console.log('===============');

    const elements = await page.locator(
      'a, button'
    ).evaluateAll(
      items =>
        items
          .map(el => ({
            tag: el.tagName,
            text: (el.innerText || '')
              .trim()
              .replace(/\s+/g, ' '),
            href: el.href || '',
            className:
              typeof el.className === 'string'
                ? el.className
                : '',
          }))
          .filter(x => x.text)
          .slice(0, 80)
    );

    for (const item of elements) {
      console.log(
        `${item.tag} | ${item.text} | ${item.href} | ${item.className}`
      );
    }

    console.log();
    console.log(
      'Browser remains open for 15 seconds.'
    );

    await page.waitForTimeout(15000);

  } catch (error) {
    console.log();
    console.log('ERROR');
    console.log('=====');
    console.log(error.message);
  }

  await browser.close();

  console.log();
  console.log('Detail probe complete.');
})();
