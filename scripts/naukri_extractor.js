const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const FIXTURES_DIR = path.join(ROOT, 'data', 'fixtures', 'naukri');

const JOB_URL =
  'https://www.naukri.com/job-listings-site-reliability-engineer-tata-consultancy-services-bengaluru-8-to-10-years-190626026419';

fs.mkdirSync(FIXTURES_DIR, { recursive: true });

function clean(text) {
  return (text || '')
    .replace(/ /g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function cleanLines(text) {
  return (text || '')
    .replace(/ /g, ' ')
    .split('\n')
    .map(x => x.trim())
    .filter(Boolean);
}

(async () => {
  const browser = await chromium.launch({
    headless: false,
  });

  const context = await browser.newContext();
  const page = await context.newPage();

  console.log('NAUKRI READ-ONLY EXTRACTOR');
  console.log('==========================');

  try {
    await page.goto(JOB_URL, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    });

    await page.waitForTimeout(5000);

    const lines = cleanLines(
      await page.locator('body').innerText()
    );

    const body = lines.join('\n');

    const title = clean(
      await page.locator('h1').first().innerText()
        .catch(() => '')
    );

    const company = clean(
      await page.locator(
        'a[href*="-jobs-careers-"]'
      ).first().innerText()
        .catch(() => '')
    );

    const location = clean(
      await page.locator(
        'a[href*="jobs-in-"]'
      ).first().innerText()
        .catch(() => '')
    );

    const applyButton = await page.locator(
      'button'
    ).filter({
      hasText: 'Apply'
    }).count();

    const jobDescriptionIndex = lines.findIndex(
      x => x.toLowerCase() === 'job description'
    );

    const aboutCompanyIndex = lines.findIndex(
      x => x.toLowerCase() === 'about company'
    );

    let jdLines = [];

    if (
      jobDescriptionIndex >= 0 &&
      aboutCompanyIndex > jobDescriptionIndex
    ) {
      jdLines = lines.slice(
        jobDescriptionIndex + 1,
        aboutCompanyIndex
      );
    }

    const jdText = clean(jdLines.join('\n'));

    const experienceMatch = body.match(
      /\b\d+\s*-\s*\d+\s*years\b/i
    );

    const postedMatch = body.match(
      /Posted:\s*[^\n]+/i
    );

    const workModel =
      lines.find(x =>
        /hybrid|remote|work from office|wfo/i.test(x)
      ) || '';

    const skillSectionIndex = lines.findIndex(
      x => x.toLowerCase() === 'key skills'
    );

    let skillText = '';

    if (skillSectionIndex >= 0) {
      skillText = lines
        .slice(
          skillSectionIndex + 1,
          Math.min(
            skillSectionIndex + 15,
            lines.length
          )
        )
        .join(' ');
    }

    const result = {
      source: 'NAUKRI',
      company,
      title,
      location,
      work_model: clean(workModel),
      job_url: JOB_URL,
      application_url: JOB_URL,
      posted_date: clean(postedMatch?.[0] || ''),
      jd_text: jdText,
      experience_required:
        clean(experienceMatch?.[0] || ''),
      mandatory_skills: [],
      preferred_skills: clean(skillText),
      apply_button_detected: applyButton > 0,
    };

    const html = await page.content();

    const htmlFixturePath = path.join(FIXTURES_DIR, 'detail_valid.html');
    const jsonFixturePath = path.join(FIXTURES_DIR, 'detail_valid.expected.json');

    fs.writeFileSync(htmlFixturePath, html, 'utf-8');
    fs.writeFileSync(
      jsonFixturePath,
      JSON.stringify(result, null, 2),
      'utf-8'
    );

    console.log();
    console.log('EXTRACTED JOB');
    console.log('=============');

    console.log(
      JSON.stringify(result, null, 2)
    );

    console.log();
    console.log(`HTML fixture : ${htmlFixturePath}`);
    console.log(`JSON fixture : ${jsonFixturePath}`);

    console.log();
    console.log(
      'PASS: Naukri detail page extracted without '
      + 'login or application interaction.'
    );

  } catch (error) {
    console.error();
    console.error('ERROR');
    console.error('=====');
    console.error(error.message);
    process.exitCode = 1;
  }

  await browser.close();
})();
