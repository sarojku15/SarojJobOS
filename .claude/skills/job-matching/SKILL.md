---
name: job-matching
description: Explain a job's match score, priority tier, and skill gaps for the current JobOS candidate — covers both "why did this job match/score this way" (job-matching) and "what does this job's JD actually require vs my profile" (job-analysis; folded in here rather than duplicated, since both read the exact same score_explanation data). Use when the user asks to understand, compare, or filter scored results, or asks about a specific job's fit.
---

# Job Matching & Analysis

Reads and explains the **existing** score already computed by
`scripts/score_job.py`/`scripts/score_explanation.py` — never
re-scores, estimates, or guesses a score in natural language. If
`job-matching` and `job-analysis` sound like two different needs,
they're not, in this codebase: a result's `score_explanation` already
contains both the score breakdown and the strong-matches/gaps analysis,
so one skill covers both rather than two skills reading the same field.

## When to use

- "Why did this job score X?"
- "What am I missing for this role?" / "skill gaps for this job"
- "Show me jobs scoring above N" / "show only Priority A jobs"
- "Compare these two jobs"
- "Analyze this JD." / "What are the main requirements?" / "How well
  does this job match my profile?" (job/JD analysis — see step 5 below
  for exactly what data this can and can't answer today)

## When not to use

- Running a new search (→ jobos-orchestrator / job-discovery-engine).
- Changing a job's application status (→ application-tracker).

## Inputs

`candidate_id`, and either a `search_id` (to look at a whole result
set) or a specific `job_id` within one.

## Workflow

1. `GET /api/searches/{search_id}/results?candidate_id={id}` — each
   result already includes `score`, `priority`, `eligible`,
   `report_status`, and the score explanation (`strong_matches` /
   `gaps` — see `docs/SCORING.md` for exactly which of the 10
   dimensions produced each entry).
2. For "why this score": walk through `docs/SCORING.md`'s 10
   dimensions against the specific `strong_matches`/`gaps` values
   returned — do not paraphrase into a made-up formula.
3. For "skill gaps": read `gaps` directly — these are dimensions where
   the job asked for something not found in the candidate's own
   confirmed profile. Never infer a gap that isn't in that list, and
   never claim a skill is present that isn't in the profile.
4. For filtering/sorting/comparing: operate on the already-returned
   JSON client-side (e.g. filter by `score > 80`) — never call a
   different or invented endpoint.
5. For "analyze this JD" / "main requirements" / "how well does this
   match": **be honest about what data is actually available.** The
   results JSON (step 1) gives you `matched_skills`/`missing_skills`
   (the derived analysis) and `job_url`, but **not** the job's raw
   `jd_text` or its stated `mandatory_skills`/`preferred_skills` lists
   — those exist in the database and in the Excel export's "JD Text"
   column (→ `job-report` skill), but this JSON endpoint doesn't
   expose them today. So: answer "how well does this match" and "what
   am I missing" directly from `matched_skills`/`missing_skills`; for
   "what are the main requirements" or a full JD read, tell the user
   you'd need either the export (ask `job-report` for it) or the real
   posting at `job_url` — don't guess at requirements text you haven't
   actually read.

## Known data-source limitation

**Do not describe a JD's full requirements text as if you read it**
unless you actually did (via the Excel export or `job_url`) — the
per-job "how well does this match" answer from the results API alone
is limited to the already-computed `matched_skills`/`missing_skills`,
not the source JD text itself.

## APIs / Tools

`GET /api/searches/{search_id}/results?candidate_id={id}` (the only
data source this skill needs — it never queries the database directly).

## Safety

- Never invent a skill match or gap not present in the real
  `score_explanation` data.
- Never imply a score guarantees an interview/offer outcome — it's
  decision support (see `docs/SCORING.md`'s Limitations section).
- Never change eligibility, priority, or status as a side effect of
  "explaining" a job.

## Output

A plain-language explanation citing the real matched/missing items,
optionally with the numeric score/priority, never a fabricated
narrative disconnected from the actual `score_explanation` payload.

## Examples

- "Explain why these jobs match me." → per-job strong_matches/gaps walkthrough.
- "Show me skill gaps for this job." → the job's `gaps` list, plainly stated.
- "Which of these is the strongest match?" → sort/compare by real `score`.
- "Analyze this JD." → matched/missing skills from the API, plus an
  honest note that the full JD text itself would need the export or
  `job_url` (see "Known data-source limitation" above).
- "What are the main requirements?" → same honesty caveat; answer from
  `missing_skills`/`matched_skills` if that's sufficient, otherwise say
  so rather than fabricating requirement text.
- "How well does this job match my profile?" → `score` + `priority` +
  the real matched/missing breakdown.
