# Live 11-source search validation — role: Senior Site Reliability Engineer, location: Bengaluru

Run against an isolated temp DB (`jobos_live_test.db`, never `jobos.db`/`jobos_dev.db`) via the real `api/main.py` app and the real `source_registry` — no mock/fake adapters, no monkeypatching. All 4 direct adapters made real network calls; all 7 search-provider-backed boards went through the real, currently-configured search-provider layer (You.com — the only one of the 3 configured keys that reported `AVAILABLE` for these 7 boards at the time of this run).

- Candidate: `cand_eb6268581791` (test candidate, confirmed profile: Senior SRE, 11y, Bengaluru)
- Search: `search_8da3f822909e`
- Run: `801312d9-eab6-46b1-a215-665575d09c3c`
- Status: **COMPLETED**, 11/11 queries completed, elapsed 499.4s (~8m19s)
- Raw JSON evidence: `live_11_source_role_sre_bengaluru_2026-09-25.json`

## Per-source table

| Source | Selected | Planned | Attempted | Method | Provider | Raw | Eligible | API results | Final status |
|---|---|---|---|---|---|---|---|---|---|
| NAUKRI | yes | yes | yes | direct adapter | — | 20 | 5 | 5 | SUCCESS |
| HIRIST | yes | yes | yes | direct adapter | — | 9 | 6 | 6 | SUCCESS |
| IIMJOBS | yes | yes | yes | direct adapter | — | 4 | 3 | 3 | SUCCESS |
| APNA | yes | yes | yes | direct adapter | — | 75 | 69 | 69 | SUCCESS |
| LINKEDIN_SEARCH | yes | yes | yes | search provider | You.com | 10 | 6 | 6 | SUCCESS |
| INDEED_SEARCH | yes | yes | yes | search provider | You.com | 9 | 0 | 0 | SUCCESS (zero eligible) |
| FOUNDIT_SEARCH | yes | yes | yes | search provider | You.com | 10 | 0 | 0 | SUCCESS (zero eligible) |
| INSTAHYRE_SEARCH | yes | yes | yes | search provider | You.com | 10 | 10 | 10 | SUCCESS |
| CUTSHORT_SEARCH | yes | yes | yes | search provider | You.com | 1 | 0 | 0 | SUCCESS (zero eligible) |
| WELLFOUND_SEARCH | yes | yes | yes | search provider | You.com | 10 | 10 | 10 | SUCCESS |
| SHINE_SEARCH | yes | yes | yes | search provider | You.com | 10 | 2 | 2 | SUCCESS |
| **TOTAL** | 11/11 | 11/11 | 11/11 | | | **168** | **111** | **111** | 11/11 SUCCESS |

`jobs_discovered=168`, `jobs_experience_excluded=15`, `jobs_eligible=111`, `jobs_scored=111` all self-consistent; raw-count column sums to 168, eligible-count column sums to 111, and the live `GET /api/searches/{id}/results` call independently returned exactly 111 rows whose per-source breakdown matches the eligible-count column exactly (CUTSHORT/FOUNDIT/INDEED correctly return 0 API rows, not missing rows — they were attempted and produced real raw results, just none passed eligibility for this candidate).

## What this proves

All 11 currently-registered logical boards are genuinely reachable from a normal saved search through the real pipeline (source selection → query planner → search queue → worker → direct adapter / search-provider adapter → normalization → dedup → eligibility → scoring → DB → API). None were silently skipped, none were narrowed to a subset, and a source producing zero eligible results (CUTSHORT, FOUNDIT, INDEED) is correctly distinguished from a source never attempted — there is no such case in this run; every source shows `attempted=1`.

Production DB (`data/applications/jobos.db`) confirmed byte-identical before and after this run: SHA256 `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8`, 114688 bytes.
