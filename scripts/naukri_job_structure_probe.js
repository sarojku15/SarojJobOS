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

  const searchUrl =
    'https://www.naukri.com/senior-site-reliability-engineer-jobs-in-bengaluru';

  console.log('NAUKRI JOB STRUCTURE PROBE');
  console.log('==========================');

  try {
    await page.goto(searchUrl, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    });

    await page.waitForTimeout(7000);

    const candidates = await page.locator('a').evaluateAll(
      links =>
        links
          .map(a => ({
            text: (a.innerText || '').trim().replace(/\s+/g, ' '),
            href: a.href || '',
            className: a.className || '',
          }))
          .filter(x =>
            x.href.includes('job-listings') ||
            x.href.includes('/job-listings/')
          )
          .slice(0, 20)
    );

    console.log();
    console.log(`Candidate job links: ${candidates.length}`);
    console.log();

    candidates.forEach((job, index) => {
      console.log(`JOB ${index + 1}`);
      console.log('------');
      console.log(`Text  : ${job.text}`);
      console.log(`URL   : ${job.href}`);
      console.log(`Class : ${job.className}`);
      console.log();
    });

    console.log(
      'Browser remains open for 10 seconds for inspection.'
    );

    await page.waitForTimeout(10000);

  } catch (error) {
    console.log();
    console.log('ERROR');
    console.log('=====');
    console.log(error.message);
  }

  await browser.close();

  console.log();
  console.log('Job structure probe complete.');
})();
