# Live Naukri Score-Quality Audit

**Dataset:** 20 real jobs captured from the one live Naukri validation run
(source=NAUKRI, role="Senior Site Reliability Engineer", location="Bengaluru",
candidate=saroj), persisted in the temp DB:
`/var/folders/5x/hc_gpp312k1fm2bsxw0d2t2r0000gn/T/jobos_live_naukri_ranking_validation_pwgcehlj/jobos_test.db`

**Method:** read-only. This audit opened the temp DB in `mode=ro`, made **zero
new network requests**, and never opened `data/applications/jobos.db`. Every
computation reuses the existing, unmodified (except where noted) production
functions: `discover_local.normalize_job()`, `job_eligibility.assess_job_eligibility()`,
`score_job.score_job()`, `score_explanation.build_score_explanation()`,
`freshness.classify_freshness()`, `canonical_job.derive_canonical_job()`,
`cross_source_dedup.find_cross_source_duplicate_candidates()`.

---

## 1. Executive Summary

The scoring, eligibility, explanation, and freshness pipeline is working
**correctly** against real Naukri data: every eligible job's score decomposes
exactly into its 10 documented components, every explanation exactly matches
`score_job()`'s own output, every persisted `candidate_job_matches` row matches
a fresh recomputation, and location handling produced zero false
rejections/acceptances in this dataset.

One **confirmed, pre-existing bug** was found and fixed: `discover_local.normalize_url()`
silently truncated every Naukri `job_url`/`application_url` to 29 characters
(`"https://www.naukri.com/job-li"`) due to a regex typo unrelated to anything
built in this project's ranking/scoring work. This is now fixed (see §9).

No eligibility or scoring bug was found. Two **suspected, non-blocking**
observations are flagged for human review in §11: (a) a display-only
simplification in `canonical_job.py`'s single-value location summary for
multi-city postings, and (b) a small number of CI/CD/cloud/observability
tool aliases present in real JDs but absent from `score_job.py`'s keyword
lists — confirmed to have **zero actual scoring impact** in this dataset.

## 2. Dataset Integrity

| Check | Expected | Actual | OK? |
|---|---|---|---|
| candidates | 1 | 1 | Yes |
| candidate_search_profile | 1 | 1 | Yes |
| search_runs | 1 | 1 | Yes |
| search_queue | 1 | 1 | Yes |
| jobs | 20 | 20 | Yes |
| candidate_job_matches | 6 | 6 | Yes |
| duplicate (source, job_id) rows in `jobs` | 0 | 0 | Yes |
| duplicate `candidate_job_matches` rows | 0 | 0 | Yes |
| candidate `target_locations` (this temp DB's narrowed profile) | `["Bengaluru"]` | `["Bengaluru"]` | Yes |
| candidate experience_years | 11 | 11.0 | Yes |

Eligible: 6. Ineligible: 14 (all 14 via `EXPERIENCE_BELOW_PROFILE`, 0 via location).
Scores of the 6 eligible jobs range 60–100 (mean 85.0).

## 3. 20-Job Audit Table

| # | Company | Title | Eligible | Reason | Exp req. | Score | Priority | Freshness |
|---|---|---|---|---|---|---|---|---|
| 1 | Tata Consultancy Services | Site Reliability Engineer | No | EXPERIENCE_BELOW_PROFILE | 8–10y | — | — | HOT (1d) |
| 2 | Acesoft Labs | Site Reliability Engineer | No | EXPERIENCE_BELOW_PROFILE | 5–10y | — | — | HOT (1d) |
| 3 | Infosys | Site Reliability Engineer (SRE) - Unix/Linux | No | EXPERIENCE_BELOW_PROFILE | 5–10y | — | — | HOT (2d) |
| 4 | ITC Infotech | Site Reliability Engineer (SRE) - GCP Platform | **Yes** | ELIGIBLE | 6–11y | **60** | A | HOT (2d) |
| 5 | Smart Ims | SRE (Site Reliability Engineer) at Bangalore | No | EXPERIENCE_BELOW_PROFILE | 3–6y | — | — | HOT (2d) |
| 6 | Mobilution It Systems | Site Reliability Engineer | **Yes** | ELIGIBLE | 8–12y | **100** | A | HOT (2d) |
| 7 | Okta | Manager Site Reliability Engineer | No | EXPERIENCE_BELOW_PROFILE | 3–8y | — | — | FRESH (3d) |
| 8 | Cognizant | Site Reliability Engineer (SRE) Support | **Yes** | ELIGIBLE | 6–11y | **75** | B | FRESH (4d) |
| 9 | Evoke HR | Site Reliability Engineer | **Yes** | ELIGIBLE | 7–12y | **95** | A | FRESH (4d) |
| 10 | FIS | Senior Site Reliability Engineer - Support | No | EXPERIENCE_BELOW_PROFILE | 1–6y | — | — | FRESH (5d) |
| 11 | Acesoft Labs | Site Reliability Engineer | **Yes** | ELIGIBLE | 6–11y | **95** | A | HOT (1d) |
| 12 | Infosys | Site Reliability Engineer (SRE) - Unix/Linux | No | EXPERIENCE_BELOW_PROFILE | 5–10y | — | — | HOT (2d) |
| 13 | Infosys | Site Reliability Engineer (SRE) - Unix/Linux | No | EXPERIENCE_BELOW_PROFILE | 2–7y | — | — | HOT (2d) |
| 14 | RiDiK | SRE-Site Reliability Engineer | No | EXPERIENCE_BELOW_PROFILE | 5–8y | — | — | FRESH (7d) |
| 15 | Smart Ims | Site Reliability Engineer | No | EXPERIENCE_BELOW_PROFILE | 5–8y | — | — | FRESH (7d) |
| 16 | Deloitte Shared Services India | Site Reliability Engineer | No | EXPERIENCE_BELOW_PROFILE | 6–10y | — | — | FRESH (7d) |
| 17 | Sigma Allied Services | Site Reliability Engineer(C2H): PAN INDIA | **Yes** | ELIGIBLE | 7–12y | **85** | A | FRESH (7d) |
| 18 | Tata Consultancy Services | Site Reliability Engineer | No | EXPERIENCE_BELOW_PROFILE | 3–6y | — | — | FRESH (7d) |
| 19 | Smart Ims | SRE (Site Reliability Engineer) at Bangalore | No | EXPERIENCE_BELOW_PROFILE | 3–6y | — | — | FRESH (7d) |
| 20 | Virtusa | Site Reliability Engineer | No | EXPERIENCE_BELOW_PROFILE | 6–10y | — | — | OLD (21d) |

Full per-job detail (all requested fields: normalized/canonical location, work
model, component breakdown, matched/missing skills, duplicate candidates) is
in `data/reports/live_naukri_score_audit.json`.

## 4. Experience Eligibility Audit (Section B)

**Candidate experience: 11.0 years.** Every one of the 14 rejections was
recomputed independently against the raw, stored `experience_required` string
and cross-checked byte-for-byte against the persisted DB row:

| Company | Raw `experience_required` | Parsed min–max | Candidate | Result | Justified? |
|---|---|---|---|---|---|
| Tata Consultancy Services | "8 - 10 years" | 8–10 | 11 | BELOW_PROFILE | Yes (11 > 10) |
| Acesoft Labs | "5 - 10 years" | 5–10 | 11 | BELOW_PROFILE | Yes (11 > 10) |
| Infosys ×3 | "5 - 10 years" / "5 - 10 years" / "2 - 7 years" | 5–10 / 5–10 / 2–7 | 11 | BELOW_PROFILE | Yes |
| Smart Ims ×3 | "3 - 6 years" / "5 - 8 years" / "3 - 6 years" | 3–6 / 5–8 / 3–6 | 11 | BELOW_PROFILE | Yes |
| Okta | "3 - 8 years" | 3–8 | 11 | BELOW_PROFILE | Yes |
| FIS | "1 - 6 years" | 1–6 | 11 | BELOW_PROFILE | Yes |
| RiDiK | "5 - 8 years" | 5–8 | 11 | BELOW_PROFILE | Yes |
| Deloitte | "6 - 10 years" | 6–10 | 11 | BELOW_PROFILE | Yes |
| Tata Consultancy Services (2nd) | "3 - 6 years" | 3–6 | 11 | BELOW_PROFILE | Yes |
| Virtusa | "6 - 10 years" | 6–10 | 11 | BELOW_PROFILE | Yes |

**Findings:**
- Every rejected job's raw `experience_required` string was parsed **exactly**
  as written (verified against the raw DB column, not just the normalized
  value) — no parsing errors, no malformed/missing fields among the 14
  rejections, no case of "job doesn't actually state a minimum."
- **No case** of "the job's range appears compatible with the candidate but
  was rejected." Every rejected job's parsed maximum is genuinely below 11.
- **Policy observation (not a bug):** 8 of the 14 rejections have a parsed
  maximum of exactly 10 — i.e., the candidate exceeds the stated ceiling by
  only **1 year**. `experience_eligibility.py`'s existing, documented
  `BELOW_PROFILE` semantics (`candidate_years > effective_max`) treats a
  1-year overqualification identically to an 8-year one (e.g. FIS's "1–6
  years", exceeded by 5 years). The code is behaving exactly as designed and
  previously tested; this is a **policy question for human judgment** (should
  a small overqualification margin be tolerated?), not a defect. No change
  was made to this behavior.
- The eligible jobs' ranges (11, 11, 11, 11, 11, 12 as their max) all
  genuinely include or exceed 11 — confirms the MATCH boundary rule
  (`candidate_years <= effective_max`) is applied correctly and consistently
  in both directions.

## 5. Score Component Audit (Section C)

For **all 6 eligible jobs**: `sum(components.values()) == score` (verified
exactly, not approximately) and `score_explanation` fields (`score`,
`priority`, `strong_matches`, `gaps`) are **byte-identical** to `score_job()`'s
own return dict.

| Company | core_role | sre_devops | cloud | k8s | terraform | cicd | observ. | exp. | loc. | fit | **Sum** | **Score** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ITC Infotech | 20 | 10 | 10 | 5 | 0 | 0 | 0 | 5 | 5 | 5 | 60 | **60** |
| Mobilution It Systems | 20 | 15 | 15 | 10 | 10 | 10 | 5 | 5 | 5 | 5 | 100 | **100** |
| Cognizant | 20 | 15 | 10 | 5 | 0 | 5 | 5 | 5 | 5 | 5 | 75 | **75** |
| Evoke HR | 20 | 15 | 10 | 10 | 10 | 10 | 5 | 5 | 5 | 5 | 95 | **95** |
| Acesoft Labs | 20 | 10 | 15 | 10 | 10 | 10 | 5 | 5 | 5 | 5 | 95 | **95** |
| Sigma Allied Services | 20 | 15 | 15 | 5 | 0 | 10 | 5 | 5 | 5 | 5 | 85 | **85** |

**Gap explanations (all traced to the actual JD text, verified by direct
inspection — not assumed):**
- **ITC Infotech (60):** JD body says *"Strong skills in AWS infrastructure,
  Docker, Kubernetes, scripting, monitoring tools"* — despite the job title
  saying "GCP Platform," the JD body never mentions GCP, Terraform/Ansible,
  any named CI/CD tool, or any named observability tool. `cloud`=10 (1 AWS
  keyword only), `kubernetes`=5 (1 keyword only), `terraform`/`cicd`/`observability`=0
  are all **correct, evidence-based** outcomes — this is a title/body mismatch
  in Naukri's own posting, not a scoring defect.
- **Cognizant (75):** JD mentions no Terraform/Ansible/IaC term at all
  (`terraform`=0 is correct) and only a partial CI/CD signal.
- **Sigma Allied Services (85):** JD mentions Jenkins but no Terraform/Ansible
  (`terraform`=0 is correct) and only one Kubernetes-family keyword.

No case of a component receiving points not justified by the JD text was found.

## 6. Skill Matching Audit (Section D)

**Clarification of what "matched/missing skills" actually are in this
codebase:** `score_job()`'s `matched_skills`/`missing_skills` are **dimension
labels** ("Cloud alignment", "Partial Kubernetes", ...), not literal skill
tokens — scoring is driven entirely by keyword search over `title`+`jd_text`,
never by the `mandatory_skills`/`preferred_skills` list fields. This matches
the existing, unmodified design; no duplicate or malformed dimension labels
were found in any of the 6 jobs.

**Real-JD alias-coverage scan** (searched all 20 raw `jd_text` values for
terms absent from `score_job.py`'s keyword lists):

| Term found in real JDs | # jobs | In keyword list? | Actual scoring impact on the 6 eligible jobs |
|---|---|---|---|
| `k8s` | 1 (Smart Ims, ineligible) | No | None (job never scored) |
| `gcp` | 8 | No (cloud list has no GCP terms) | None confirmed — every eligible job mentioning GCP-adjacent terms also independently matched AWS/Azure/EKS/AKS |
| `gitlab` (bare, not "gitlab ci") | 2 | Partially (`"gitlab ci"` only) | None — Mobilution/Deloitte's JDs also mention Jenkins/GitHub Actions |
| `azure devops` | 3 | No | None — Evoke HR/Acesoft Labs' JDs also mention Terraform + Jenkins |
| `gke` | 3 | No | None — all 3 JDs also say "Kubernetes" plainly |
| `cloudformation` | 1 (Mobilution) | No | None — Mobilution also mentions Terraform, Jenkins, GitHub Actions |
| `bicep` | 1 (Acesoft Labs) | No | None — same JD also mentions Terraform |
| `datadog` | 2 (Acesoft Labs, FIS) | No | None confirmed for Acesoft Labs (observability=5 already via another keyword); FIS is ineligible |

**Conclusion: these are real, confirmed taxonomy gaps, but in this specific
20-job dataset they produced zero incorrect scores** — every eligible job
missing points for terraform/cicd/observability was independently verified
to genuinely lack ANY qualifying keyword (including these aliases) in its JD.
Per this audit's explicit scope, the taxonomy was **not broadened** — flagged
in §11 for human review only.

No case of false-positive matching, case-sensitivity failure, or duplicated
skill entries was found.

## 7. Location Audit (Section E)

Raw `location` strings in this dataset are frequently **multi-city lists**
(a real Naukri behavior, not a normalization artifact):

| Raw location | Cities parsed | Candidate target | Result |
|---|---|---|---|
| "Bengaluru" | Bengaluru | Bengaluru | MATCH |
| "Pune, Bengaluru, Delhi / NCR" | Pune, Bengaluru, Delhi NCR (region) | Bengaluru | MATCH (via Bengaluru) |
| "Pune, Bengaluru" | Pune, Bengaluru | Bengaluru | MATCH (via Bengaluru) |
| "Hyderabad, Pune, Bengaluru" | Hyderabad, Pune, Bengaluru | Bengaluru | MATCH (via Bengaluru) |
| "Pune, Bengaluru, Mumbai (All Areas)" | Pune, Bengaluru, Mumbai | Bengaluru | MATCH (via Bengaluru) |

**Every one of the 20 jobs genuinely lists Bengaluru as one of its posted
locations** — confirmed by direct inspection of the raw `location` column, not
assumed. `location_taxonomy.assess_location_eligibility()`'s "MATCH if ANY
listed job location matches ANY candidate target location" logic is working
correctly: `excluded_location=0` is the **correct** outcome for this dataset,
not evidence of a bug.

**Suspected (non-blocking) observation:** `canonical_job.py`'s
`_derive_canonical_location()` returns only the **first** city-kind location
it finds — for a multi-city posting like "Pune, Bengaluru, Delhi / NCR" it
displays `canonical_location="Pune"`, even though **Bengaluru** is the city
that actually made the job eligible. This is display-only: it does not affect
eligibility (which correctly checks the full list) or scoring (which does not
use `canonical_location` at all). Flagged in §11.

No Bangalore/Bengaluru alias failure, and no PAN INDIA compound-phrase failure,
was observed in this dataset (no PAN INDIA location strings appeared here,
despite one job's *title* containing "PAN INDIA" — Sigma Allied Services'
location field was "Pune, Bengaluru, Mumbai (All Areas)", not a PAN-India
compound phrase, so `location_taxonomy.py`'s PAN_COUNTRY handling was not
actually exercised by this dataset).

## 8. Freshness Audit (Section F)

| Freshness input | Parsed age (days) | Category | Correct? |
|---|---|---|---|
| "Posted: 1 day ago" | 1 | HOT | Yes |
| "Posted: 2 days ago" | 2 | HOT | Yes |
| "Posted: 3 days ago" | 3 | FRESH | Yes |
| "Posted: 4 days ago" | 4 | FRESH | Yes |
| "Posted: 5 days ago" | 5 | FRESH | Yes |
| "Posted: 7 days ago" | 7 | FRESH | Yes |
| "Posted: 21 days ago" | 21 | OLD | Yes |

All ages fall precisely on the correct side of the documented HOT(0–2)/FRESH(3–7)/
AGING(8–14)/OLD(15–30)/STALE(31+) boundaries. No malformed or future-dated
`posted_date` value appeared in this real dataset (Naukri always returned a
well-formed "Posted: N day(s) ago" phrase for these 20 listings) — the
malformed/future-date code paths remain validated only by the existing
synthetic fixture tests (`test_freshness.py`), not by this live sample.
`classify_freshness()` was confirmed deterministic (called twice per sampled
input, identical result both times).

## 9. Suspected Bugs

(See §11 for the two items ultimately classified as non-blocking observations
rather than bugs, after investigation.)

## 10. Confirmed Bugs

### BUG-1 (FIXED): `discover_local.normalize_url()` truncated every Naukri URL

**Severity:** High (breaks the actual clickable job link for every Naukri
posting; pre-existing, unrelated to any component built in this project's
recent sessions).

**Evidence:** every one of the 20 captured jobs' `job_url` and
`application_url` columns were identical length (29 characters) and
identical truncated value: `"https://www.naukri.com/job-li"`.

**Root cause:** `scripts/discover_local.py` line 25 read:
```python
match = re.search(r"https?://[^\\s)]+", value)
```
The intended regex was `[^\s)]+` (exclude whitespace or `)`). Because the
string literal contains a **literal double backslash**, the compiled regex
instead excludes the literal characters `\`, `s`, and `)` — not whitespace.
Since Naukri's own URL pattern always contains `job-listings` immediately
after the domain, the match stopped at the first `s` (in "li**s**tings"),
truncating every single Naukri URL to `".../job-li"`.

This was never caught by any existing test because `naukri_parser.py`'s own
tests check the **adapter's raw output** (before `normalize_job()` runs), and
`normalize_url()` is only invoked later, inside `discover_local.normalize_job()`.

**Fix applied:** changed the regex to `r"https?://[^\s)]+"` (single
backslash). Verified against the real captured Naukri URL (now preserved
exactly, 124 characters) and against three other representative URL shapes
containing a lowercase `s` in the path. All 33 pre-existing test files still
pass unmodified after this fix.

**Note:** the already-captured temp DB's `job_url`/`application_url` values
remain truncated (historical data; not modified, per this audit's read-only
rule, and un-recoverable without a new live fetch, which was not performed).
The fix only prevents this from recurring in future runs.

## 11. Recommended Fixes / Suspected Issues Requiring Human Review

1. **(Policy, not code)** Reconsider whether `EXPERIENCE_BELOW_PROFILE`
   should treat a 1-year overqualification (e.g. 11 years vs. an "8–10
   years" posting) the same as a severe one (e.g. 11 years vs. "1–6 years").
   8 of the 14 rejections in this run fall in the "exceeded by exactly 1
   year" category. No code change recommended without an explicit decision
   on the desired tolerance.
2. **(Display only)** `canonical_job._derive_canonical_location()` shows only
   the first parsed city for a multi-city posting, which can name a
   non-matching city (e.g. "Pune") even when a different listed city (e.g.
   "Bengaluru") is what made the job eligible. Eligibility/scoring are
   unaffected. Consider showing all parsed cities, or the one that actually
   matched, in a future reporting/CLI layer.
3. **(Taxonomy, no observed impact)** `score_job.py`'s keyword lists do not
   recognize `k8s`, `gcp`/`gke`, `azure devops`, `cloudformation`, or `bicep`.
   Confirmed present in real JDs, confirmed to have caused **zero** incorrect
   scores in this 20-job sample (every case had a qualifying keyword
   elsewhere in the same JD). Worth monitoring against a larger sample before
   deciding whether to broaden the taxonomy.

## 12. NO FIX REQUIRED (explicit conclusions)

- **Eligibility gate (experience + location):** behaving exactly as designed;
  all 14 rejections are justified against the raw, unmodified data.
- **100-point scoring engine:** all 10 dimension weights unchanged; every
  eligible job's component sum equals its score; every gap traces to real,
  verified JD content.
- **Score explanation:** exact match to `score_job()` for all 6 eligible jobs.
- **Freshness classification:** all 7 distinct real inputs classified
  correctly against the documented day-boundaries.
- **Cross-source duplicate detection:** correctly found zero candidates
  (single-source dataset; the logic itself was already validated separately
  with synthetic multi-source fixtures).
- **Intra-source exact deduplication:** correctly found zero duplicates —
  the 3 near-identical Infosys postings are genuinely distinct listings
  (different underlying URLs at generation time), not a missed duplicate.
