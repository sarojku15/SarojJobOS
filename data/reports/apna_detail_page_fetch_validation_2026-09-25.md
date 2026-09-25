# APNA detail-page fetch — live validation

Run against an isolated temp DB (never jobos.db/jobos_dev.db) via the real API + real `ApnaAdapter` (`max_detail_fetches_per_query=20`, the new bounded default), role="Senior Site Reliability Engineer", location="Bengaluru".

- Run: `7df3c4b4-3e19-4eb1-8b4d-dde27cac05cb`, status **COMPLETED**, elapsed 74.4s
- Listing jobs found: **75** (raw_count)
- Eligible: **69**
- Detail fetch attempted: **20** (bound respected exactly)
- Detail fetch succeeded: **19**
- Detail fetch failed: **1**
- APNA jobs with non-empty `jd_text` in the DB after this run: **19** (previously 0/1349, see the original score-audit finding)
- Source audit status: **SUCCESS**, truthfully carrying `details_json = {"detail_fetch_attempted": 20, "detail_fetch_succeeded": 19, "detail_fetch_failed": 1}` — the failed detail fetch did not fail the whole source, and is not silently reported as success.

## Sample score-component change (real, verified evidence)

The same real job — **"Site Reliability Engineer" at Infosys** (`APNA-04df26d0fbeaf9e9`, the same job manually inspected during the earlier score audit) — scored **40 before this phase** (title-only, empty `jd_text`) and now scores **55** with its real, detail-page-enriched JD text ("Evaluate and ensure availability of components... SLO... SLI/SLOs... observability platform...").

Component change: `SRE/DevOps responsibilities` went from 5 (title-only, "Limited") to a real keyword match, and `Overall/domain fit` now matches on genuine JD content ("production", "platform") instead of just the title. `sum(components) == score` still holds (verified programmatically in `test_apna_adapter.py`).

Other enriched jobs (mostly non-tech "Site Engineer"/"Site Civil Engineer" construction-industry postings — APNA is a general job board, not tech-specific) correctly remain low-scoring after enrichment: their real JD text genuinely has no cloud/k8s/iac/cicd content, so a low score is the CORRECT outcome, not a parsing gap.

## Production DB integrity

SHA256 before and after this live run: `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` — byte-identical. Never opened.

Raw evidence: `apna_detail_page_fetch_validation_2026-09-25.json`
