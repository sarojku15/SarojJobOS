# Naukri Browser-Runtime Diagnostic — Direct Headed-vs-Headless Measurement

**Method:** a new, standalone, diagnostic-only script
(`scripts/naukri_browser_runtime_probe.js`, never referenced by any
production file — confirmed by `scripts/test_browser_runtime_probe_inert.py`)
launched this project's **exact** `channel:'chromium'` configuration twice —
once `headless:false`, once `headless:true`, both otherwise identical
(same viewport `{1440, 900}`, no other context overrides) — against a
**local, in-memory-generated `file://` HTML fixture only**. Zero network
requests. Zero contact with Naukri or any external site. Verified: the
probe script contains no reference to `naukri.com` anywhere.

**This resolves the prior forensic report's biggest evidence gap**: that
report's process-capture was truncated at 300 characters for the exact
`channel:'chromium'` headed-vs-headless pairing, forcing several claims to
be labeled INFERENCE. This probe captures **complete, untruncated** process
command lines and **directly measured** runtime JS properties for that
exact pairing — several of those prior inferences are now either confirmed
as FACT, or **directly contradicted**.

## Headline Finding

**`navigator.userAgent` differs, and the difference is exactly the
best-known, most classic automated-browser detection signal there is:**

| Mode | User-Agent |
|---|---|
| Headed | `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) **Chrome**/153.0.0.0 Safari/537.36` |
| Headless | `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) **HeadlessChrome**/153.0.0.0 Safari/537.36` |

**This is FACT/OBSERVED, directly measured from this exact project
configuration — not inferred.** The literal substring `"Headless"` is
present in the User-Agent **HTTP header sent with every single request**
in headless mode, and absent in headed mode. Checking for this exact
substring is one of the single most common, well-documented anti-bot
techniques in existence — far more directly actionable by a server-side WAF
than any JS-runtime signal, since it requires no page execution at all,
only reading a standard HTTP header.

## A–D. Full Property Comparison

| Property | Headed | Headless | Difference? | Evidence type |
|---|---|---|---|---|
| `navigator.userAgent` | `...Chrome/153.0.0.0...` | `...**HeadlessChrome**/153.0.0.0...` | **YES** | FACT/OBSERVED |
| `navigator.webdriver` | `true` | `true` | No | FACT/OBSERVED |
| `navigator.platform` | `MacIntel` | `MacIntel` | No | FACT/OBSERVED |
| `navigator.vendor` | `Google Inc.` | `Google Inc.` | No | FACT/OBSERVED |
| `navigator.languages` | `["en-GB","en-US","en"]` | `["en-GB","en-US","en"]` | No | FACT/OBSERVED |
| `navigator.language` | `en-GB` | `en-GB` | No | FACT/OBSERVED |
| `navigator.hardwareConcurrency` | `12` | `12` | No | FACT/OBSERVED |
| `navigator.deviceMemory` | `16` | `16` | No | FACT/OBSERVED |
| `navigator.maxTouchPoints` | `0` | `0` | No | FACT/OBSERVED |
| `navigator.plugins.length` | `5` | `5` | No | FACT/OBSERVED |
| `navigator.mimeTypes.length` | `2` | `2` | No | FACT/OBSERVED |
| `screen.width` / `screen.height` | 1440 / 900 | 1440 / 900 | No | FACT/OBSERVED |
| `screen.availWidth` / `availHeight` | 1440 / 900 | 1440 / 900 | No | FACT/OBSERVED |
| `window.innerWidth` / `innerHeight` | 1440 / 900 | 1440 / 900 | No | FACT/OBSERVED |
| `window.outerWidth` / `outerHeight` | **1442 / 980** | **1440 / 900** | **YES (small)** | FACT/OBSERVED |
| `devicePixelRatio` | 1 | 1 | No | FACT/OBSERVED |
| `screen.colorDepth` / `pixelDepth` | 24 / 24 | 24 / 24 | No | FACT/OBSERVED |
| `matchMedia('(pointer: fine)')` | `true` | `true` | No | FACT/OBSERVED |
| `matchMedia('(pointer: coarse)')` | `false` | `false` | No | FACT/OBSERVED |
| `matchMedia('(hover: hover)')` | `true` | `true` | No | FACT/OBSERVED |
| `matchMedia('(hover: none)')` | `false` | `false` | No | FACT/OBSERVED |
| `matchMedia('(any-pointer: fine)')` | `true` | `true` | No | FACT/OBSERVED |
| `matchMedia('(any-hover: hover)')` | `true` | `true` | No | FACT/OBSERVED |
| WebGL available | `true` | `true` | No | FACT/OBSERVED |
| WebGL vendor | `WebKit` | `WebKit` | No | FACT/OBSERVED |
| WebGL renderer | `WebKit WebGL` | `WebKit WebGL` | No | FACT/OBSERVED |
| WebGL version | `WebGL 1.0 (OpenGL ES 2.0 Chromium)` | identical | No | FACT/OBSERVED |
| WebGL unmasked vendor | `Google Inc. (Apple)` | `Google Inc. (Apple)` | No | FACT/OBSERVED |
| WebGL unmasked renderer | `ANGLE (Apple, ANGLE Metal Renderer: Apple M4 Pro, Unspecified Version)` | **identical** | No | FACT/OBSERVED |

**Important correction to the prior forensic report:** that report
*inferred* (not confirmed) that headless mode might use software
(SwiftShader) WebGL rendering, distinguishable from headed mode's real GPU
path, and that the `--blink-settings` touch-emulation flag would make
headless report a touch/no-hover input profile. **Both of those inferences
are now directly contradicted by measurement**: WebGL renderer is
byte-identical (real "ANGLE Metal" hardware rendering in both modes), and
every pointer/hover media query returns identical (non-touch, hover-capable)
results in both modes. These are not the differentiators.

## E. Browser Process — Complete, Untruncated Argument Diff

**Directly captured (FACT/OBSERVED) — complete command lines for this
exact `channel:'chromium'` pairing, no truncation:**

**Flags present ONLY in the headless main process** (confirmed FACT — not
inferred, unlike the prior report):
```
--headless
--hide-scrollbars
--mute-audio
--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4
```

**Flags present ONLY in the headed main process:** none of substance (only
the per-launch, randomly-generated `--user-data-dir=` temp path differs,
which is expected and not a configuration difference).

**Correction to the prior report:** `--enable-unsafe-swiftshader` is
present in **both** headed and headless main-process command lines this
time (directly observed) — it is **not** a headless-specific flag in this
Playwright/Chromium version, contrary to the prior report's inference. This
is consistent with the WebGL measurement above showing identical, real
hardware rendering in both modes.

**GPU helper process**, also directly captured:
- Headed: `...Google Chrome for Testing Helper (GPU)... --type=gpu-process --no-sandbox --disable-breakpad --enable-unsafe-swiftshader --user-data-dir=...`
- Headless: `...Google Chrome for Testing Helper (GPU)... --type=gpu-process --no-sandbox --disable-breakpad --headless --enable-unsafe-swiftshader --noerrdialogs --user-data-dir=...`

Headless-only GPU-helper flags: `--headless`, `--noerrdialogs`.

## F. Chromium Identity

| | Headed | Headless |
|---|---|---|
| Executable path | `chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing` | **identical** |
| Version (from UA) | `153.0.0.0` (browser build); framework version `153.0.8010.12` | identical |
| Channel | `chromium` | `chromium` |
| Binary identity | Same file | Same file |

**Confirmed FACT: identical executable in both modes** — this rules out a
binary-identity difference conclusively (reconfirms the prior report's
finding, now with a full untruncated capture rather than a partial one).

## Direct Answers

1. **Does `navigator.webdriver` differ?** No — `true` in both. FACT.
2. **Does WebGL renderer/vendor differ?** No — byte-identical in both,
   including the unmasked (real hardware) renderer string. FACT. (This
   contradicts the prior report's inference.)
3. **Does `navigator.maxTouchPoints` differ?** No — `0` in both. FACT.
4. **Do pointer/hover capabilities differ?** No — every `matchMedia()`
   query returns identical results in both modes, despite the
   `--blink-settings` flag's presence only in headless. FACT.
5. **Does viewport/window/screen geometry differ?** Viewport, inner
   dimensions, and `screen.*` are identical. `window.outerWidth/outerHeight`
   differ by a small amount (1442×980 headed vs. 1440×900 headless) — the
   real OS window frame overhead present only in headed mode. FACT (minor).
6. **Does the actual Chromium executable differ?** No — identical path,
   confirmed directly. FACT.
7. **Does the actual Chromium version differ?** No — identical. FACT.
8. **What launch arguments differ?** `--headless`, `--hide-scrollbars`,
   `--mute-audio`, and the touch/hover `--blink-settings` flag (main
   process); `--headless`, `--noerrdialogs` (GPU helper). All FACT, fully
   captured this time.
9. **Which differences are directly captured versus inferred?** **All of
   the above are directly captured (FACT)** for this exact configuration —
   this diagnostic was built specifically to eliminate the inference gap
   from the prior report.
10. **Which differences could plausibly explain the 403?** See below —
    carefully separating observation from relevance from causality.

## Observation vs. Plausible Relevance vs. Demonstrated Causality

**Observed differences (FACT):**
- `navigator.userAgent` contains `"HeadlessChrome"` vs. `"Chrome"`.
- `window.outerWidth/outerHeight` differ by a small margin.
- Four launch flags differ (`--headless`, `--hide-scrollbars`,
  `--mute-audio`, touch/hover `--blink-settings`), with no observed
  runtime-JS effect from the last one.

**Plausible relevance (reasoned judgment, not proof):**
- The **User-Agent difference is the single most plausible candidate**,
  because unlike every other measured property, it is transmitted
  automatically as a standard HTTP header on **every** request (including
  the very first request of a session), requires zero JS execution to
  check, and the literal substring `"Headless"` is one of the most
  historically common, explicitly-documented signals web servers and WAFs
  check for.
- `window.outerWidth/outerHeight` are not typically sent to a server or
  used in HTTP-layer decisions; they would only matter to JS running on the
  page itself, which is far less likely to be Naukri's first line of
  defense for a 403 returned before any interactive page logic would
  plausibly run.
- The four launch-flag differences have no demonstrated runtime-JS
  consequence in this measurement (pointer/hover queries were identical) —
  their *plausible relevance* to a server-side block is low, since a remote
  server cannot see Chromium's launch flags directly; it can only see their
  downstream effects, which here were not observed to differ (with the
  narrow exception of `outerWidth`/`outerHeight`, addressed above).

**Demonstrated causality: none.** No experiment in this project has varied
the User-Agent (or any other single property) in isolation against a live
Naukri request to confirm it is the actual trigger. **This report does not
claim the User-Agent difference causes the 403** — it identifies it as the
most evidence-supported, plausible candidate given everything measured so
far, clearly distinct from a proven cause.

## Legitimate Background-Execution Options (Not Implemented)

| Option | Feasible on this macOS machine? | Avoids a user-visible browser? | Changes Chromium's headed/headless mode? | Additional validation required | Preserves the background-only requirement? |
|---|---|---|---|---|---|
| **1. Native Chromium headless** (current default) | Yes — already in use | Yes | N/A (this IS headless) | None further needed for "no visible window"; the open problem is Naukri access, not visibility | Yes, but currently non-functional against Naukri |
| **2. Headed Chromium + Xvfb (virtual display)** | **No** — Xvfb is an X11/Linux mechanism; this project runs on Darwin (macOS), confirmed via this session's environment. No native equivalent exists on macOS. | Yes, if run on a supported (Linux) host | **Yes — uses genuinely headed Chromium**, which is the one configuration proven live to succeed against Naukri | Would require an actual Linux host/container (this project already has `docker-compose.yml` for other services, so this is architecturally plausible as a future deployment target) and a fresh live test from that environment | Yes, if implemented on Linux |
| **3. Another Playwright-supported background/display configuration on this environment** | Partially — Playwright supports launching with a custom `args` list, which could in principle drop or alter the `--headless`-triggered flags identified above (e.g. a custom user-agent override via `context.newContext({ userAgent: ... })`, which Playwright explicitly supports as a standard, documented API for legitimate testing purposes) | Depends on the specific mechanism | Could remain in native headless mode while adjusting individual reported properties via Playwright's own supported context options | A live test would be required to determine whether adjusting the User-Agent alone (the most plausible candidate identified here) has any effect | Yes, if it works, since it stays within native headless mode |

**No stealth plugin, anti-detection library, or fingerprint-spoofing
technique is being proposed here.** Option 3, specifically, would use
Playwright's own **standard, documented, first-party** `userAgent` context
option — the same mechanism used for entirely legitimate purposes like
cross-browser testing (e.g. simulating a specific real browser/OS
combination for compatibility testing). Using it to present a real,
accurate "Chrome" user agent instead of an artifact string that only exists
because of *how* the browser was launched (not because of anything false
being claimed about the browser's actual capabilities) sits closer to
"choosing an accurate representation" than to "spoofing" — but this
distinction is a judgment call, not a settled technical fact, and is
explicitly flagged for your own consideration rather than decided here.

## Recommended Next Step

**Not implemented in this task.** If you wish to pursue this further, the
lowest-risk next step would be a single, carefully-scoped live comparison
where the **only** variable changed is the User-Agent string in headless
mode (via Playwright's standard `context.newContext({ userAgent: <the
exact headed-mode string already measured above> })` option) — everything
else (channel, viewport, `headless: true`, advisory architecture, single
query) held identical to the most recent blocked headless test. This is
the most surgical possible next experiment given everything measured here.

**This report does not execute that experiment.**

## Regression / Safety

- New file `scripts/naukri_browser_runtime_probe.js`: diagnostic-only,
  confirmed by `scripts/test_browser_runtime_probe_inert.py` to be
  unreferenced by any production file and to contain no reference to
  `naukri.com`.
- New test file `scripts/test_browser_runtime_probe_inert.py`: passes.
- Full existing standalone suite: **37/37 pass** (36 pre-existing + 1 new).
- `py_compile`: clean.
- **No production code was modified.** `naukri_fetch_bridge.js`,
  `naukri_fetcher.py`, `naukri_adapter.py`, and every scoring/eligibility/
  ranking/dedup/freshness/worker file remain byte-for-byte unchanged.

## Production DB Safety

| | SHA-256 | Row counts |
|---|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | identical |

**Byte-identical. Zero Naukri/network requests were made. Two browser
launches occurred, both against a local `file://` fixture only.**
