# Controlled Live Validation — 4 Direct-Enabled Sources

One single search query per source (`role="Senior Site Reliability Engineer"`,
`location="Bengaluru"`), each adapter's own default rate-limit settings, no
production DB access (`data/applications/jobos.db` was not opened by this
validation — SHA256/size confirmed identical before and after). Raw output
and per-source JSON detail: `phase_remaining_items_4_direct_source_live_validation.json`.

Method: real `search()` call per adapter, then the first returned job was
run through the actual unmodified pipeline (`discover_local.normalize_job()`
→ `job_ranking.build_ranking_record()`, i.e. eligibility + scoring +
freshness + canonical-location derivation) to prove the adapter's real
output doesn't break the unified pipeline — not just that a page loaded.

## Results

| Source  | Status  | Jobs found | Pipeline (normalize→eligibility→score→freshness) | Notes |
|---------|---------|-----------:|----------------------------------------------------|-------|
| NAUKRI  | SUCCESS | 21 | OK, no error | Sample job: all fields present (title/company/location/canonical URL/experience/posted date). Sampled job correctly EXPERIENCE_BELOW_PROFILE (job wanted less experience than an 11-yr candidate) — pipeline decision, not an adapter defect. |
| HIRIST  | SUCCESS | 9  | OK, no error | Sample job's `experience_required` was empty on this posting (Hirist doesn't always expose it) — correctly UNKNOWN downstream, not guessed. Freshness UNKNOWN (no posted-date text on this listing) — correctly not fabricated. |
| IIMJOBS | SUCCESS | 4  | OK, no error | Small result count and one loosely-relevant hit (e.g. an unrelated exec role) reflect IIMJOBS' own search relevance for this query, not a parsing/pipeline bug — scored low (28) and would not qualify, exactly as intended. `canonical_location` correctly `null` (unparseable on this listing) rather than guessed. |
| APNA    | SUCCESS | 75 | OK, no error | Largest result volume of the four. Sample job had no `posted_date` (Apna listing didn't expose one) — freshness correctly UNKNOWN, not fabricated. |

## What this confirms

- All 4 adapters reach their real target sites: `health_check()` reported
  `reachable=True, block_reason=NONE` for every source.
- No blocks, CAPTCHAs, or off-domain/listing-page corruption were observed
  on any of the 4 single-query runs.
- Canonical job URLs are absolute, real URLs for every source (verified on
  the sampled job from each).
- Company/title/location are parsed when the source actually supplies them;
  when a field (experience, posted date, location) is genuinely absent on a
  real listing, it comes through as empty/UNKNOWN rather than guessed —
  confirmed live, not just in offline fixtures.
- A real job from every one of the 4 sources passes cleanly through
  `normalize_job()` → `build_ranking_record()` (eligibility, scoring,
  freshness, `requirement_type`, canonical location) with no exception —
  the unified pipeline is not broken by any of the 4 adapters' real output.
- Production DB (`data/applications/jobos.db`) was never opened; SHA256
  and size confirmed identical before and after.

## Not covered by this run (out of scope, per the controlled-validation instructions)

- No multi-query / broader coverage run was performed (deliberately kept to
  the smallest reasonable number of queries — one per source).
- No restricted-board (search-provider) sources were touched.
- No unrelated provider/source issue was investigated or "repaired."
