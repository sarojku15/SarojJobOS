# 11-source live validation — FINAL (post-APNA-enrichment)

Re-run of the full 11-source live sweep after Phase 1 (APNA detail-page fetching). Isolated temp DB, real API + real adapters, role="Senior Site Reliability Engineer", location="Bengaluru".

- Run: `8590b393-80c8-463a-93b9-9a5740c1aca3`, status **COMPLETED**, elapsed 592.8s
- `jobs_discovered=169`, `jobs_eligible=112` — matches the sum of every source's own raw/eligible counts exactly, and matches `api_results_count_total=112` exactly.

## Per-source table

| Source | Attempted | Status | Raw | Eligible | API results |
|---|---|---|---|---|---|
| NAUKRI | yes | SUCCESS | 20 | 5 | 5 |
| HIRIST | yes | SUCCESS | 10 | 7 | 7 |
| IIMJOBS | yes | SUCCESS | 4 | 3 | 3 |
| APNA | yes | SUCCESS | 75 | 69 | 69 |
| LINKEDIN_SEARCH | yes | SUCCESS | 10 | 6 | 6 |
| INDEED_SEARCH | yes | SUCCESS | 9 | 0 | 0 |
| FOUNDIT_SEARCH | yes | SUCCESS | 10 | 0 | 0 |
| INSTAHYRE_SEARCH | yes | SUCCESS | 10 | 10 | 10 |
| CUTSHORT_SEARCH | yes | SUCCESS | 1 | 0 | 0 |
| WELLFOUND_SEARCH | yes | SUCCESS | 10 | 10 | 10 |
| SHINE_SEARCH | yes | SUCCESS | 10 | 2 | 2 |
| **TOTAL** | **11/11** | **11/11 SUCCESS** | **169** | **112** | **112** |

**APNA's audit additionally carries real detail-fetch metrics** (`details_json`): `{"detail_fetch_attempted": 20, "detail_fetch_succeeded": 19, "detail_fetch_failed": 1}` — truthfully reported in the same live sweep as every other source, proving Phase 1's enrichment is wired into the full pipeline, not just APNA-in-isolation.

## Run_id consistency (mandatory check)

`run_id` is identical across every view checked: the top-level run response, `run_detail.search_run_id`, and every one of the 11 `search_run_sources` rows' own `search_run_id`. No cross-run mixing — the same run drives results, summary counts, and source audit throughout.

## Production DB

SHA256 before and after: `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` — byte-identical. Never opened.

Raw evidence: `live_11_source_validation_FINAL_2026-09-25.json`
