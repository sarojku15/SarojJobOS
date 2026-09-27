---
name: interview-prep
description: Generate, review, and update deterministic, evidence-based interview preparation for a specific JobOS job — questions across Kubernetes/Cloud/Terraform/CI-CD/Observability/Incident-management/SLI-SLO/Behavioral/Leadership/Company-specific/Resume-based categories, each paired with a suggested answer grounded in the candidate's own confirmed profile, or an honest "Preparation required" note where no evidence exists. Use when the user asks to prepare for an interview, review interview prep, save an answer, or record an interview outcome.
---

# Interview Preparation

Wraps the real, persisted interview-prep pipeline
(`scripts/interview_prep.py`, via
`POST /api/candidates/{id}/interview-prep`). **Fully deterministic —
no LLM call anywhere in this backend.** The question bank is a static,
curated list of genuinely common industry questions per category (the
questions themselves make no claim about the candidate, so there's no
fabrication risk there). The **suggested answer** for each question is
where honesty is enforced by construction:

- If the candidate's own confirmed `employment_history` genuinely
  mentions the category's relevant keywords, the suggested answer
  quotes a real, verbatim sentence from their own resume, with the
  employer named.
- If not, the suggested answer is the literal string **"Preparation
  required — no demonstrated experience found."** — never an invented
  claim that the candidate has experience they don't.
- Company-specific questions only appear when a real, linked
  `company_research` record with actual content exists — skipped
  entirely otherwise, never fabricated.

**Always call these "suggested answers" (or "evidence-based suggested
answers") — never "model answers."** "Model answer" implies an
authoritative correct answer; what this actually produces is either a
real quote from the candidate's own resume or an honest gap admission.

## When to use

- "Prepare me for the interview for this job."
- "Regenerate my interview prep now that I have a tailored resume /
  company research for this job."
- "What questions should I expect for this Kubernetes-heavy role?"
- "Save my answer to this question." / "Mark this interview as passed."
- "Show my existing interview prep for this job."

## When not to use

- Presenting a suggested answer as proof the candidate has experience
  they don't — if `has_demonstrated_experience` is `false`, say so
  plainly ("Preparation required"), never smooth it into a confident
  answer.
- Generating interview prep for a job this candidate isn't eligible
  for — the backend enforces the same eligibility gate as scoring; a
  404 here means "not eligible," not a bug.

## Inputs

`candidate_id`, `job_id`, and optionally a `resume_id` and/or
`company_research_id` to ground the prep in a specific tailored resume
version or company research record.

## Workflow

1. Confirm the profile is `CONFIRMED`
   (`GET /api/candidates/{id}/profile`) — required before generating.
2. Generate/regenerate:
   `POST /api/candidates/{id}/interview-prep` with
   `{"job_id": ..., "resume_id": ..., "company_research_id": ...}`
   (both optional). A second call for the same `(candidate, job)`
   **regenerates the same record** (an explicit refresh, not a
   duplicate) — old questions are replaced, never silently kept stale
   alongside new ones.
3. Present the real response: `overview` (matched/missing skills, same
   scoring engine as everywhere else), `checklist`, `questions_to_ask`,
   `skill_gap_plan`, and the question list grouped by category — each
   with its real `has_demonstrated_experience` flag and suggested
   answer text, verbatim from the API.
4. **Save a candidate's own answer**:
   `PATCH /api/candidates/{id}/interview-prep-questions/{question_id}`
   with `{"candidate_answer": ..., "confidence": 1-5, "notes": ...}` —
   only when the user actually dictates their own answer; never invent
   one on their behalf.
5. **Record an outcome** after the real interview:
   `PATCH /api/candidates/{id}/interview-prep/{id}/outcome` with
   `{"outcome_status": ..., "outcome_notes": ...}` — only when the user
   tells you what actually happened, same rule as application-tracker's
   status writes.
6. **Retrieve existing prep**:
   `GET /api/candidates/{id}/jobs/{job_id}/interview-prep`.

## APIs / Tools

`POST /api/candidates/{id}/interview-prep`,
`GET /api/candidates/{id}/jobs/{job_id}/interview-prep`,
`GET /api/candidates/{id}/interview-prep/{id}`,
`PATCH /api/candidates/{id}/interview-prep-questions/{question_id}`,
`PATCH /api/candidates/{id}/interview-prep/{id}/outcome`.

## Safety

- Never call a suggested answer a "model answer" — the correct terms
  are "suggested answer" / "evidence-based suggested answer."
- Never imply the candidate has experience/skills not present in their
  confirmed profile — a `false` `has_demonstrated_experience` flag
  means "Preparation required," full stop.
- Never invent a candidate's own answer, confidence level, or interview
  outcome — only ever record what the user explicitly states.
- Company-specific questions must never be presented if no real
  `company_research` content was actually linked.

## Output

The real, persisted prep record and question list from the API —
category, question text, suggested answer, evidence flag, and (once
saved) the candidate's own answer/confidence/notes/outcome.

## Examples

- "Prepare me for this interview." → generate/fetch, present by
  category, flag which answers are evidence-based vs. "Preparation
  required."
- "I don't have Terraform experience, what should I say if asked?" →
  read the real Terraform question's suggested answer (should already
  say "Preparation required...") and suggest an honest framing, never
  a fabricated Terraform anecdote.
- "I passed the interview, mark it." →
  `PATCH .../outcome` with `outcome_status: "PASSED"`.
