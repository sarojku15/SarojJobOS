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
