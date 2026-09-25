// Offline browser-runtime diagnostic probe -- NOT part of the production
// Naukri adapter/worker pipeline (like the other naukri_*_probe.js
// exploratory scripts, this file is never imported or invoked by
// naukri_fetcher.py, naukri_adapter.py, or search_worker.py).
//
// Purpose: directly measure runtime/browser-fingerprint properties for
// THIS project's exact channel='chromium' launch configuration, in both
// headed and headless mode, against a LOCAL file:// fixture only.
//
// Usage: node scripts/naukri_browser_runtime_probe.js
//
// Makes ZERO network requests. Never contacts Naukri or any external
// site. Never modifies naukri_fetch_bridge.js's or naukri_fetcher.py's
// actual behavior -- this is a read-only, standalone measurement tool.
//
// Context options mirror naukri_fetch_bridge.js exactly (viewport
// 1440x900, channel: 'chromium', no other explicit overrides) so the
// ONLY intentional difference between the two launches below is the
// `headless` boolean itself.

const { chromium } = require('playwright');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execSync } = require('child_process');

const FIXTURE_HTML = `<!doctype html>
<html><head><title>Runtime Probe</title></head>
<body><script>
function safeMatchMedia(query) {
  try { return matchMedia(query).matches; } catch (e) { return null; }
}

function webglInfo() {
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    if (!gl) return { available: false };
    const info = {
      available: true,
      vendor: gl.getParameter(gl.VENDOR),
      renderer: gl.getParameter(gl.RENDERER),
      version: gl.getParameter(gl.VERSION),
      unmaskedVendor: null,
      unmaskedRenderer: null,
    };
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    if (ext) {
      info.unmaskedVendor = gl.getParameter(ext.UNMASKED_VENDOR_WEBGL);
      info.unmaskedRenderer = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
    }
    return info;
  } catch (e) {
    return { available: false, error: e.message };
  }
}

window.__PROBE_RESULT__ = {
  navigator: {
    userAgent: navigator.userAgent,
    webdriver: navigator.webdriver,
    platform: navigator.platform,
    vendor: navigator.vendor,
    languages: navigator.languages ? Array.from(navigator.languages) : null,
    language: navigator.language,
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemory: navigator.deviceMemory !== undefined ? navigator.deviceMemory : null,
    maxTouchPoints: navigator.maxTouchPoints,
    pluginsLength: navigator.plugins ? navigator.plugins.length : null,
    mimeTypesLength: navigator.mimeTypes ? navigator.mimeTypes.length : null,
  },
  screenWindow: {
    screenWidth: screen.width,
    screenHeight: screen.height,
    screenAvailWidth: screen.availWidth,
    screenAvailHeight: screen.availHeight,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    outerWidth: window.outerWidth,
    outerHeight: window.outerHeight,
    devicePixelRatio: window.devicePixelRatio,
    colorDepth: screen.colorDepth,
    pixelDepth: screen.pixelDepth,
  },
  pointerInput: {
    pointerFine: safeMatchMedia('(pointer: fine)'),
    pointerCoarse: safeMatchMedia('(pointer: coarse)'),
    hoverHover: safeMatchMedia('(hover: hover)'),
    hoverNone: safeMatchMedia('(hover: none)'),
    anyPointerFine: safeMatchMedia('(any-pointer: fine)'),
    anyHoverHover: safeMatchMedia('(any-hover: hover)'),
  },
  webgl: webglInfo(),
};
</script></body></html>`;

function captureProcessLines(marker) {
  try {
    const out = execSync('ps aux').toString();
    return out
      .split('\n')
      .filter((line) => line.toLowerCase().includes('chromium') && !line.toLowerCase().includes('grep') && !line.includes(marker));
  } catch (e) {
    return [`<ps failed: ${e.message}>`];
  }
}

async function runOne(headless, marker) {
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

  const fixturePath = path.join(os.tmpdir(), `naukri_runtime_probe_${headless ? 'headless' : 'headed'}.html`);
  fs.writeFileSync(fixturePath, FIXTURE_HTML, 'utf-8');

  await page.goto(`file://${fixturePath}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(500);

  const result = await page.evaluate(() => window.__PROBE_RESULT__);

  // Capture the FULL, untruncated process command line while the
  // browser is still open -- this is the direct evidence gap the prior
  // forensic report flagged (that report's captures were truncated at
  // 300 characters). No slicing here.
  const processLines = captureProcessLines(marker);

  await browser.close();
  fs.unlinkSync(fixturePath);

  return { headless, result, processLines };
}

(async () => {
  const headedResult = await runOne(false, '__PROBE_MARKER_NEVER_MATCHES__');
  const headlessResult = await runOne(true, '__PROBE_MARKER_NEVER_MATCHES__');

  console.log(JSON.stringify({ headed: headedResult, headless: headlessResult }, null, 2));
})().catch((e) => {
  console.error('PROBE ERROR:', e.message);
  process.exit(1);
});
