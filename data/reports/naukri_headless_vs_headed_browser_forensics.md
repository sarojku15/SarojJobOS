# Naukri Headed-vs-Headless Chromium — Technical Difference Forensics

**Scope:** pure offline analysis of this project's existing code, captured
process/command-line evidence, and this session's own artifacts. **Zero new
Naukri/network requests. Zero browser launches.** No stealth, anti-detection,
fingerprint-spoofing, or evasion technique is proposed anywhere below — every
option considered is a legitimate, documented browser/deployment
configuration choice.

## Important Evidence-Completeness Caveat (read first)

This project's process-evidence capture has evolved across tasks, and this
matters for what can be claimed here:

- One earlier live run (the very first headless attempt, using Playwright's
  **default** `chromium_headless_shell` binary — not `channel:'chromium'`)
  captured **complete, untruncated** process command lines.
- Every later run (including the two most recent `channel:'chromium'`
  headless-vs-headed runs being compared here) truncated captured command
  lines to **300 characters**, and both truncated at the **identical**
  point (mid-way through a shared prefix of flags common to every Chromium
  launch, headed or headless).

**Consequence:** the FULL flag list for the specific "headed vs. headless,
both `channel:'chromium'`" comparison is **not directly captured** — only
the full flag list for the unrelated "default headless-shell binary" run
is. Where this matters, it is called out explicitly below as an INFERENCE
(by analogy to Playwright's consistent, version-wide internal
argument-construction logic, which builds the headless-conditional flag set
independently of which `channel` is ultimately resolved to an executable),
not a directly observed FACT for this exact pairing.

## 1. Chromium Executable

| | Headed run | Headless run(s) | Label |
|---|---|---|---|
| Path | `.../ms-playwright/chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing` | **identical path**, confirmed for the `channel:'chromium'` headless run | FACT |
| Version/revision | `1243` | `1243` | FACT |
| Channel | `chromium` (explicit) | `chromium` (explicit, unchanged) | FACT |
| Binary type | Full "Google Chrome for Testing" build | **same build** — confirmed by identical executable path and by 0 `chromium_headless_shell` observations across both runs | FACT |
| Same executable used? | **Yes — confirmed identical.** | | FACT |

This directly **rules out** a binary-identity difference as the
explanation (already established in the prior forensic task, reconfirmed
here).

## 2. Chromium Launch Arguments

**Directly observed (FACT), from the one run with an untruncated
capture — the default `chromium_headless_shell` headless launch:**

```
--disable-field-trial-config --disable-background-networking
--disable-background-timer-throttling --disable-backgrounding-occluded-windows
--disable-back-forward-cache --disable-breakpad
--disable-client-side-phishing-detection
--disable-component-extensions-with-background-pages
--disable-component-update --no-default-browser-check --disable-default-apps
--disable-dev-shm-usage --disable-edgeupdater --disable-extensions
--disable-features=... --enable-features=CDPScreenshotNewSurface
--allow-pre-commit-input --disable-hang-monitor
--disable-ipc-flooding-protection --disable-popup-blocking
--disable-prompt-on-repost --disable-renderer-backgrounding
--disable-updater-scheduler --force-color-profile=srgb
--metrics-recording-only --no-first-run --password-store=basic
--use-mock-keychain --no-service-autorun --export-tagged-pdf
--disable-search-engine-choice-screen
--unsafely-disable-devtools-self-xss-warnings
--edge-skip-compat-layer-relaunch --disable-infobars --disable-sync
--enable-unsafe-swiftshader --headless --hide-scrollbars --mute-audio
--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4
--no-sandbox --user-data-dir=... --remote-debugging-pipe --no-startup-window
```

**The headless-specific flags, isolated (FACT for this default-binary run;
INFERENCE that the same flags apply to the `channel:'chromium'` headless run,
per Playwright's documented, version-consistent argument-building logic,
which is not conditioned on `channel`):**

| Flag | What it does | Fingerprint relevance |
|---|---|---|
| `--headless` | The core mode switch | Directly signals automation intent to Chromium itself |
| `--enable-unsafe-swiftshader` | Forces **software** WebGL/GPU rendering (SwiftShader) instead of real hardware acceleration | **Well-known, publicly documented headless-detection vector** — a page can call `WebGLRenderingContext.getParameter(UNMASKED_RENDERER_WEBGL)` and see `"Google SwiftShader"`/`"Software Rasterizer"` instead of a real GPU vendor string |
| `--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4` | Emulates a **touch-primary, no-hover** input device (values 2/4 correspond to Blink's "coarse pointer, no hover" enums) | Detectable via CSS `@media (hover: hover)` / `@media (pointer: fine)` or JS `matchMedia()` — a real desktop Chrome reports fine-pointer/hover-capable; this makes headless report a touch-device profile instead |
| `--hide-scrollbars` | Suppresses scrollbar rendering | Minor, rendering-only |
| `--mute-audio` | Silences audio | Not typically fingerprint-relevant |
| `--no-startup-window` | Prevents opening a default blank window | Structural, not detectable by a remote page |
| `--remote-debugging-pipe` | CDP over a pipe rather than a TCP port | Not directly observable by a remote page |

**Not observed to differ (FACT, from the shared 300-char prefix present in
both truncated captures):** every flag up through
`--disable-search-engine-choice-screen`/`--disable-sync` region is
byte-identical between the headed and headless `channel:'chromium'`
captures — confirming these are NOT the differentiator (they're present in
both).

**NOT DETERMINABLE:** whether `--enable-unsafe-swiftshader` and the
touch-emulation `--blink-settings=...` flag were present or absent in the
**headed** `channel:'chromium'` run specifically — the capture for that run
was truncated before reaching that portion of the argument list. By general
Chromium/Playwright knowledge (not this project's own captured evidence):
a headed browser rendering to a real, visible OS window on a real display
typically has genuine GPU compositor access and would not need the
SwiftShader software fallback, and does not use touch-input emulation by
default — but this is **inference from general, public Playwright/Chromium
behavior**, not a fact this project's own artifacts confirm for this exact
run.

## 3. Browser Context

| Setting | Value (both headed and headless) | Label |
|---|---|---|
| Viewport | `{width: 1440, height: 900}` | FACT — identical code, `naukri_fetch_bridge.js`'s `context = await browser.newContext({viewport: {...}})` call is unchanged and unconditional on headless/headed |
| User agent | never explicitly set | FACT (no override in code, either mode) |
| Locale / timezone | never explicitly set | FACT |
| Color scheme / reduced motion | never explicitly set | FACT |
| Permissions | never explicitly set (Playwright defaults) | FACT |
| JavaScript | enabled (default, never disabled) | FACT |
| Cookies / storage | none persisted, ever, in either mode | FACT |
| Service workers | never configured | FACT |
| Cache | Playwright default (fresh profile every launch, via `--user-data-dir` pointing at a fresh temp dir each time) | FACT |
| Proxy | never configured | FACT |
| Extra HTTP headers | never configured | FACT |

**Conclusion: the browser CONTEXT configuration in this project's own code
is 100% identical between headed and headless calls** — `naukri_fetch_bridge.js`
passes the exact same `newContext({viewport:...})` call regardless of the
`headless` variable's value. Any behavioral difference must come from
Chromium's own internal headless-mode behavior (§2, §4), not from anything
this project's context-creation code does differently.

## 4. Page/Runtime Signals

**NOT DETERMINABLE from this project's own artifacts** — no test in this
project has ever run `page.evaluate()` to read `navigator.webdriver`,
`navigator.userAgent`, `navigator.platform`, `navigator.vendor`,
`navigator.languages`, `screen.*`, WebGL renderer strings,
`navigator.hardwareConcurrency`, or `navigator.deviceMemory` in either
headed or headless mode. None of this project's captured HTML fixtures or
diagnostic output includes any of these values.

**General, publicly documented Playwright/Chromium knowledge (explicitly
NOT project-specific evidence, offered only as context):**
- `navigator.webdriver` is patched to `true` by Playwright/Puppeteer-style
  automation **in both headed and headless modes equally** — this is a
  constant across both of this project's own runs, so it **cannot** explain
  why headed succeeded and headless failed (it doesn't differ between them).
- WebGL renderer string is the one signal most plausibly affected by
  `--enable-unsafe-swiftshader` specifically (§2) — if that flag is indeed
  headless-only for this project's launches (an inference, not confirmed),
  this would be a genuine, real runtime difference a remote page could
  detect.
- Screen/window dimensions can differ in headless mode (no real OS window
  frame exists) even when the Playwright-level `viewport` setting is
  identical — again, general knowledge, not confirmed by this project's own
  captured data.

## 5. Playwright Behavior

| | Value | Label |
|---|---|---|
| Playwright package version | `1.63.0` | FACT — unchanged all session (`package.json`, confirmed via `node -e "require('playwright/package.json').version"` earlier this session) |
| Browser type | `chromium` | FACT |
| `channel: 'chromium'` used | Yes, in both the compared headed and headless runs | FACT |
| Do headed/headless use different binaries despite the same channel? | **No** — confirmed identical executable path in both (§1) | FACT |
| Known launch-configuration difference visible in current code | Only the `headless` boolean itself (`const headless = process.env.JOBOS_BROWSER_HEADLESS !== '0'`) — no other conditional branch exists in `naukri_fetch_bridge.js` | FACT |

**This is the crux finding:** this project's own code contains **exactly
one** explicit configuration difference between the two modes — the
`headless` boolean passed to `chromium.launch()`. Everything else this
project's code controls (channel, viewport, context options, navigation
strategy, timeouts, settle delay) is provably identical. Any behavioral
difference therefore originates from **Chromium's own internal response to
the `headless` flag** (which flags/behaviors it enables internally, per §2
and §4), not from anything else this project has configured.

## Hypothesis Evaluation

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| A | Different Chromium **binary** | **CONTRADICTED** | Identical executable path confirmed in both runs |
| B | Different Chromium **version** | **CONTRADICTED** | Identical revision `1243` in both |
| C | Headless-specific **launch arguments** | **SUPPORTED (by inference, not direct capture for this exact pairing)** | Directly observed (FACT) in the default-binary headless run: `--headless`, `--enable-unsafe-swiftshader`, touch-emulation `--blink-settings=...`, `--hide-scrollbars`, `--no-startup-window`. Inferred (not directly captured, due to truncation) to also apply to the `channel:'chromium'` headless run, since Playwright's flag-construction logic is not channel-conditional. |
| D | `navigator.webdriver`/runtime **automation-flag** difference | **CONTRADICTED as a distinguishing factor** | Per general Playwright behavior, this is patched identically in both headed and headless modes — it does not differ between the two, so it cannot explain why one succeeded and the other failed. (Actual value never captured by this project either way.) |
| E | **Viewport/screen/window** difference | **CONTRADICTED at the code level; NOT DETERMINABLE at the runtime level** | The Playwright `viewport` context option is identical in code (FACT). Whether Chromium's own reported `screen.*`/window dimensions differ internally between headed/headless was never measured by this project (NOT DETERMINABLE). |
| F | **GPU/WebGL/rendering** difference | **SUPPORTED (by inference)** | `--enable-unsafe-swiftshader` directly observed in the default-binary headless launch (FACT); its presence specifically in the compared headless run is inferred, not directly captured; its absence in the headed run is inferred from general Chromium behavior (real window → real compositor), also not directly captured. This is the single most concrete, well-documented, code-visible candidate available from this project's own evidence, but remains an inference for the exact pairing being compared. |
| G | **HTTP header** difference | **NOT DETERMINABLE** | No header capture exists anywhere in this project, for any run |
| H | Browser-**context** difference | **CONTRADICTED** | Context creation code is byte-identical and unconditional on headless/headed (§3) |
| I | **Playwright-specific** behavior (beyond the headless flag itself) | **NOT DETERMINABLE beyond the headless flag itself** | No other Playwright-level configuration differs; the headless flag's downstream effects on Chromium (§2, §4) are the only avenue |
| J | Other concrete difference | **NOT DETERMINABLE** | No further difference identified from available evidence |
| K | Root cause still not determinable from offline evidence | **PARTIALLY TRUE** | The *mechanism class* is narrowed to Chromium's own internal headless-mode behavior (flags/rendering), specifically C and F — but the *exact* signal Naukri's server-side detection actually keys on cannot be determined from outside its system, by definition, regardless of how much client-side evidence is gathered |

## Legitimate Background-Execution Options (Not Executed)

The stated target architecture is:
`BACKGROUND AUTOMATION → NO USER-VISIBLE BROWSER → FULL CHROMIUM →
Naukri-compatible execution characteristics → existing pipeline → results`.

### Reframing "headless" first — this matters for every option below

**The actual operational requirement, from your own original complaint
("I do NOT want visible browser windows... no Chrome window, no Naukri
tab..."), is about a HUMAN never seeing a browser window / the automation
running in the background — not literally Chromium's internal
`headless=true` API flag.** These are two different concepts that happen to
usually coincide, but do not have to. Chromium's native `--headless` mode
is one way to satisfy "no visible window"; it is not the only way. This
reframing is what makes the options below meaningful.

| Option | Description | Satisfies "no visible window"? | Uses genuine full-Chromium (headed) rendering characteristics? | Evasion? | Risk/trade-off |
|---|---|---|---|---|---|
| **1. Xvfb + headed Chromium** | Run actual headed Chromium against an off-screen virtual X11 display (`Xvfb`) | Yes — Xvfb renders to a virtual framebuffer, nothing appears on any real screen | **Yes** — real hardware/software compositor path matching genuine headed behavior, no `--headless` flag at all | No — this is a standard, extremely common, legitimate pattern used by countless CI/scraping systems worldwide; it is literally "run a real browser without a physical monitor," not fingerprint spoofing | **Not available on macOS** — Xvfb is an X11/Linux concept; this project currently runs on Darwin (confirmed via this session's own environment). Would require migrating actual browser execution to a Linux host/container — this project already has `docker-compose.yml` for other services, so Linux deployment is architecturally plausible, but this is a real infrastructure change, not a code tweak |
| **2. macOS background session** | Run the existing headed configuration, but under a machine/user session where no interactive person is watching (e.g. a dedicated automation Mac, a non-interactive login session) | Only in the sense that no human happens to be looking — the window still technically exists and could appear if someone did look | Yes — literally the existing, already-proven-working headed configuration | No | macOS has no first-class equivalent to Xvfb for a fully headless virtual display; a real WindowServer session is still involved. Practical but not a clean architectural guarantee of invisibility (e.g., screen sharing into that session would reveal the window) |
| **3. Tune headless-mode rendering flags** | Keep `headless: true`, but override Playwright's default args to avoid `--enable-unsafe-swiftshader` in favor of real GPU access (e.g. via Playwright's `args` option, if the host has genuine GPU access available even without a visible window) | Yes — stays in native headless mode | Partially — real hardware rendering instead of the software fallback, while everything else about "headless" (no window, no `--enable-unsafe-swiftshader`-driven touch/pointer emulation if that can also be adjusted) is retained | **No — this is picking a real, standard Chromium rendering backend over a documented software fallback, not fabricating or spoofing anything.** | Untested and unverified whether this changes Naukri's behavior at all (F is an inference, not a confirmed cause); GPU access inside a headless/background process is itself environment-dependent and may not be available on every deployment target |
| **4. Linux container headless (native)** | Run natively on Linux, where Chromium's headless implementation has historically had somewhat different internals/maturity than macOS's | Yes | Uncertain — still Chromium headless, just a different OS | No | Doesn't address the flag-level differences identified in §2; likely no better than the current macOS headless result without also combining with Option 1 or 3 |

**No option above involves stealth plugins, fingerprint spoofing, CAPTCHA
bypass, proxy rotation, or cookie manipulation.** All are legitimate
deployment/configuration choices.

## Recommended Next OFFLINE Implementation Step

**Not implemented in this task, per instruction.** If you choose to pursue
this further, the lowest-risk, most information-dense next OFFLINE step
would be:

1. Add a **diagnostic-only** (non-behavior-changing) enhancement to
   `naukri_fetch_bridge.js`: after page load, call
   `page.evaluate(() => ({ webdriver: navigator.webdriver, renderer:
   (() => { try { const gl = document.createElement('canvas').getContext('webgl'); return gl.getParameter(gl.getExtension('WEBGL_debug_renderer_info').UNMASKED_RENDERER_WEBGL); } catch(e) { return null; } })(), hover: matchMedia('(hover: hover)').matches, pointerFine: matchMedia('(pointer: fine)').matches }))`
   and log the result to stderr as another `DIAGNOSTIC` line (same pattern
   already established for status/URL). This would let the **next** live
   experiment directly confirm or refute whether the WebGL renderer string
   and hover/pointer capabilities actually differ between headed and
   headless in this project's own environment — converting §2/§4's
   INFERENCE-labeled claims into FACT for this project specifically,
   entirely offline-testable (against a local fixture) before any live use.
2. This diagnostic addition is small, additive, does not touch the
   classifier, does not touch scoring/eligibility/ranking, and would not by
   itself change whether Naukri blocks anything — it only makes the next
   live comparison more conclusive.

## What Must Be Tested Live Afterward

- With the diagnostic from step 1 in place, ONE more single-query live
  comparison (headed vs. headless, same query as before) would directly
  confirm whether the WebGL renderer / hover-pointer signals actually
  differ in practice for this project's exact launch configuration —
  resolving hypothesis F/C from INFERENCE to FACT or CONTRADICTED.
- If Option 1 (Xvfb) is ever pursued, a live test from that environment
  would be needed to confirm it actually reproduces headed-mode success
  while remaining invisible.
- If Option 3 (tuned headless rendering flags) is ever pursued, a live test
  is the only way to know whether it changes the outcome at all.

**None of this is executed by this report.**

## Should We Change the Default Browser Configuration Now?

**No — not yet, per instruction, and not yet justified by the evidence
either.** The evidence narrows the *mechanism class* (Chromium's internal
headless-mode flags/rendering) but does not yet confirm the *exact* signal,
and no option above has been live-tested. Changing the automated default
before that confirmation would risk trading a known-safe (if currently
non-functional) headless default for an unverified alternative.

## Production DB Safety

| | SHA-256 | Row counts |
|---|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | identical |

**Byte-identical. No live Naukri/network request was made. No browser was
launched. No code was changed.**
