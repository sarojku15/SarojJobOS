# Scoping note: APNA detail-page fetching (not implemented — future task)

## Why this matters

Found during a live score audit (2026-09-25): **100% of APNA jobs in the dev DB (1349/1349) have empty `jd_text`**. `scripts/apna_adapter.py` explicitly documents this as intentional: `_MAX_DETAIL_FETCHES_PER_QUERY = 0 -- no detail-page fetching implemented this phase -- jd_text/posted_date are left empty, never guessed`.

`score_job.py`'s 100-point rubric spends 70 points (SRE/DevOps responsibilities 15, Cloud 15, Kubernetes 10, Terraform/IaC 10, CI/CD 10, Observability 5, Overall/domain fit 5) matching candidate skill keywords against `f"{title} {jd_text}"`. With `jd_text` always empty, every APNA job can only ever earn those 70 points from whatever happens to appear in its title — systematically suppressing scores regardless of true fit. Confirmed live: after fixing a stale/miscategorized test candidate profile, a real search against APNA-sourced results still produced **zero qualified (A/B/C) jobs out of 287 hard-eligible ones**, with the top score only 28/100.

APNA is currently the single largest contributor among the 4 direct-adapter sources — 69 of 111 eligible jobs in an 11-source live run performed the same day (see `live_11_source_role_sre_bengaluru_2026-09-25.md`). This makes it the highest-leverage single fix available for systemic score quality, larger in impact than any change to the scoring rubric itself.

## What would be required (not done, needs explicit approval per this project's own adapter-change process)

1. **Inspect current APNA detail-page behavior** — is a detail page reachable per-job without auth? Check robots.txt, rate limits, any CAPTCHA/Cloudflare challenge infrastructure (this project's established practice before touching any live adapter, see CLAUDE.md's "Source Adapter Principle" phased-validation section).
2. **Extend `apna_adapter.py`** to optionally fetch each result's detail page (bounded — `_MAX_DETAIL_FETCHES_PER_QUERY` already exists as a config knob, currently hardcoded to 0) and parse the real JD body into `jd_text`, mirroring `naukri_parser.py`'s existing detail-page pattern.
3. **Respect budget/rate limits** — fetching N detail pages per query multiplies APNA's request volume; needs its own cooldown/budget consideration in `daily_search_planner.py`, not assumed free.
4. **Offline tests first** (fixture-based, no live calls) — normalization, failure handling (missing field, timeout, blocked detail page), exactly like every other adapter's testing principle in CLAUDE.md.
5. **Phased live validation** — one live query, then a controlled multi-query run, only then consider marking the new capability live — the same process Naukri/Hirist/IIMJobs/Apna's own `search` capability already went through.
6. **Re-run this same score audit afterward** to confirm scores now meaningfully differentiate real fit once `jd_text` is populated.

## Explicitly not started

No code changes were made for this item. This is a scoping note only, per your instruction to defer implementation to a future, explicitly-scoped task.
