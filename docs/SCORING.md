# Scoring & Matching

This documents the **real** implementation in `scripts/score_job.py` and
`scripts/score_explanation.py`. Nothing below is aspirational — every
number and rule here is read directly from that code.

## Two separate gates, in order

1. **Eligibility gate** (`scripts/job_eligibility.py`,
   `scripts/experience_eligibility.py`) — experience range and location,
   run *before* scoring. An ineligible job is never scored or shown as
   a match, no matter how well its text matches. This is the one
   authoritative eligibility check every ingestion path shares.
2. **Mandatory requirement gate** (`evaluate_hard_reject()` in
   `scripts/score_job.py`) — a job is force-rejected, regardless of
   score, if:
   - its title contains one of *your own* `excluded_roles` (from your
     profile's job preferences — never a hardcoded list),
   - its title looks junior/intern-level, or
   - it lists a `mandatory_skills` entry that doesn't appear anywhere
     in your profile's listed skills (cloud, Kubernetes, IaC, CI/CD,
     observability, SRE, programming, tools).

   **A high numerical score never overrides this gate.**

Only a job that survives both gates is scored.

## The 100-point score

10 named dimensions, computed against **your own** confirmed profile
(never a fixed, single-candidate list):

| # | Dimension | Points | Compared against |
|---|---|---|---|
| 1 | Core role alignment | 20 | Your `target_roles` (word-overlap, with SRE/"Site Reliability Engineer" style acronym handling) |
| 2 | SRE/DevOps responsibilities | 15 | Generic keywords: SLO/SLI/SLA, error budget, incident, reliability, observability, production support, RCA (≥4 matches = full 15, ≥2 = 10, ≥1 = 5) |
| 3 | Cloud (AWS/Azure/etc.) | 15 | Your listed `cloud` skills (≥2 matches = 15, ≥1 = 10) |
| 4 | Kubernetes | 10 | Your listed `kubernetes` skills (≥2 = 10, ≥1 = 5) |
| 5 | Terraform/IaC | 10 | Your listed `iac_and_automation` skills (≥1 = 10) |
| 6 | CI/CD | 10 | Your listed `cicd_and_devops` skills (≥2 = 10, ≥1 = 5) |
| 7 | Observability | 5 | Your listed `observability` skills (≥1 = 5) |
| 8 | Experience | 5 | The *same* eligibility assessment used by the gate above — this dimension can never disagree with it. Unparseable requirement = benefit of the doubt (full 5 points), not a penalty. |
| 9 | Location/work model | 5 | Your `target_locations`, or "Remote" in the job's location/work model |
| 10 | Overall/domain fit | 5 | Generic keywords: production, cloud, platform, microservices, infrastructure, distributed systems, reliability (≥2 = 5) |

**Total: 100 points.** Dimensions 1, 3, 4, 5, 6, 7 (70 of the 100
points) are profile-specific — the *same* job genuinely scores
differently for two different candidates. Dimensions 2 and 10 (20
points) are still generic job-quality signals, not yet tied to a
specific profile field.

## Priority tiers

| Score range | Priority |
|---|---|
| 90–100 | **A** |
| 80–89 | **B** |
| 70–79 | **C** |
| Below 70 | Reject |

(`config/application_schema.json`'s `priority_rules`.)

## Explanation, not a black box

`scripts/score_explanation.py` turns the raw score into a human-readable
`strong_matches` / `gaps` breakdown — this is exactly what powers the
"Matched Skills" and "Gaps" columns in both the results UI and the
Excel export. Nothing in the explanation is generated separately from
the score; it's a direct readout of which dimensions above did or
didn't award points.

## Custom Job Scoring Profiles (optional, per-candidate)

The 100-point model above is the **default** and is always available
with no setup. If you want to define your own weighted criteria
instead, `scripts/scoring_profiles.py` (persisted via
`scripts/scoring_profile_store.py`, UI at `/settings/scoring-profiles`)
extends the same engine rather than replacing it — it reuses
`score_job.py`'s own mandatory-gate logic, keyword matching and
experience-eligibility result unchanged.

**Nothing changes for you unless you create and select a profile.** A
saved search with no scoring profile selected scores exactly as
described above, byte-for-byte, forever.

### Creating a profile

From `/settings/scoring-profiles`: name it, set a weight (any positive
number — they don't need to total 100) for each of 7 criteria, turn the
mandatory-skill gate on or off, and set a shortlist threshold. A
suggested illustrative starting point:

| Criterion | Illustrative weight |
|---|---|
| Technical skills | 30% |
| Relevant experience | 20% |
| Role/title alignment | 15% |
| Salary/compensation | 15% |
| Location/work mode | 10% |
| Certifications | 5% |
| Company/domain | 5% |

This is a **starting point, not a requirement** — every candidate is
free to use any distribution of positive weights across these 7
criteria.

### Weight normalization

Weights are normalized to 100% at evaluation time — you do not need
them to sum to exactly 100 when you save a profile. A weight of 0,
negative, or non-numeric is rejected at save time; a profile whose
weights are all 0 (nothing left to normalize) is also rejected.

### Weights vs. the mandatory gate — kept strictly separate

A criterion's *weight* only affects the weighted score. It never makes
that criterion mandatory. The mandatory-skill gate is a **separate,
binary** check (on by default, reusing `score_job.py`'s own
`evaluate_hard_reject()` unchanged): if a job's own listed
`mandatory_skills` aren't in your profile, the job is `REJECT` and
`qualified=false` regardless of its weighted score — a 95/100 weighted
score can never override a failed gate.

### Missing data policy

Some criteria (salary, certifications, company/domain, and location
when your profile preferences are empty) depend on data a given job
posting or candidate profile may not provide. The policy is explicit
and uniform across every criterion: missing data is never scored as a
mismatch (0%) and never fabricated as a full match (100%) — it is
scored as neutral partial credit, and the result is flagged
`missing_data: true` with a stated reason, visible in the
profile-preview breakdown. Certifications and Company/domain are
explicitly **text-mention proxies** (keyword matches against the job's
own description), not a verified structured match.

### The two thresholds, and precedence

A saved search has its own, pre-existing `minimum_match_score` field.
A scoring profile has its own `min_shortlist_score`. These are **not
combined or compared against each other**:

- **No profile selected (default):** the search's `minimum_match_score`
  keeps its existing, purely informational role — it is stored and
  displayed, but (as today, unchanged by this feature) it does not
  itself gate or filter which jobs appear.
- **A profile selected:** that profile's own `min_shortlist_score`,
  together with the mandatory gate, is the sole determinant of
  `qualified` for every job in that search. The search's own
  `minimum_match_score` remains visible for reference but has no
  effect while a profile is selected.

The UI shows both numbers side by side on the search form so this
distinction is never hidden.

### Selecting a profile for a saved search

Set `scoring_profile_id` on a saved search (via the search form at
`/searches/new` / `/searches/{id}/edit`, or the API). `scoring_profile_id:
null` (explicit, not omitted) reverts a search to default scoring.

If a selected profile is later deleted, the next real run of that
search **fails explicitly** (`search_runs.status = 'FAILED'`, with a
clear `error_message` naming the missing profile) rather than silently
scoring with default scoring under the custom profile's name — you
always know which model actually produced a given run's results.

### When scores are recomputed

Every real search run scores jobs fresh, using whichever model
(default, or the search's currently-selected profile) applies at that
moment — this was already true before this feature and is unchanged.

**Historical rescoring of already-persisted matches is not
implemented.** Changing a profile's weights, or selecting a different
profile on an existing saved search, only affects that search's *next*
run. A job already scored and persisted keeps the score, priority and
`scoring_profile_id`/version it was actually scored with — never
retroactively relabeled as having come from a newer profile version.
`candidate_job_matches` and `candidate_job_search_matches` both record
`scoring_profile_id` + `scoring_profile_version` (`NULL`/`NULL` for a
default-scored match) for exactly this traceability.

### Interpreting criterion explanations

A profile preview (`POST /api/candidates/{id}/scoring-profiles/preview`,
also available from the UI) returns, per criterion: its raw score
(0–1), its normalized weight, its weighted contribution to the final
0–100 score, whether it was `missing_data`, and the matched keywords
(if any) that produced it. These always sum to the final score — there
is no hidden adjustment between the criterion breakdown and the number
shown.

### Returning to legacy/default scoring

Clear the saved search's scoring profile selection (set
`scoring_profile_id` to `null`) — the next run uses the default
100-point model again, unchanged.

### Cross-source consistency

Every source's raw job dict passes through the same
`discover_local.normalize_job()` before scoring, same as today —
`score_with_profile()` never branches on `source`, so two otherwise-
identical jobs from different boards always score identically under
the same profile.

### Known limitations

- Historical rescoring is not implemented (see above) — this is a
  deliberate, documented limitation, not an oversight.
- Certifications and Company/domain are keyword-mention proxies against
  job-description text, not a verified structured match.
- A saved search's own `minimum_match_score` has no effect once a
  custom profile is selected (see precedence rule above).

## Limitations, stated plainly

- This is **decision support, not a guarantee.** A high score does not
  mean you will get an interview or an offer; a low score does not
  mean you shouldn't apply if you believe the job is a fit.
- 2 of the 10 dimensions (worth 20 points) are generic text-quality
  signals, not yet compared against a specific profile field.
- The mandatory-skill check does simple substring matching against your
  profile's skill lists — it does not understand synonyms or implied
  skills beyond what's explicitly written there.
- Only your own uploaded/entered profile is ever used. See
  [JOB_SOURCES.md](JOB_SOURCES.md) for what data each source actually
  supplies into this scoring, and [APPLICATION_LIFECYCLE.md](APPLICATION_LIFECYCLE.md)
  for what happens after a job is scored.
