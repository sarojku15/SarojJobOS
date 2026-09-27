---
name: resume-manager
description: List, upload, and explain resume/profile version selection for the current JobOS candidate — which resumes exist, which one scored a given result, how to pin a search to a specific resume/profile version, and how to generate an ATS-oriented tailored resume version for a specific job. Use when the user asks about their resumes, resume versions, which resume was used for a job/search, or asks to "tailor my resume for this job."
---

# Resume Manager

Wraps the real resume-storage and profile-versioning system
(`scripts/resume_store.py`, `scripts/candidate_profile.py`) and the
real resume-tailoring pipeline (`scripts/resume_tailoring.py`, via
`POST /api/candidates/{id}/jobs/{job_id}/tailor-resume`).

**Tailoring is deterministic, ATS-oriented reordering and emphasis of
the candidate's own existing profile/resume content — never AI
rewriting, never invented experience or skills.** It reorders the
professional summary's own sentences, reorders the candidate's own
skill list, and extracts the candidate's own existing employment-
history sentences into a highlights view, all so job-relevant content
appears first. Every word in the output already existed in the
candidate's confirmed profile; an automated `factual_safety_status`
check (PASS/REJECTED) verifies this before anything is persisted.

## When to use

- "What resumes do I have uploaded?"
- "Upload this resume."
- "Which resume was used to score this job?"
- "Pin this search to my old resume instead of my current one."
- "Tailor my resume for this job." / "Generate a tailored resume for
  the Akamai SRE role."
- "Show me the tailored resume versions for this job."

## When not to use

- Asking for an actually-*rewritten*, paraphrased, or newly-worded
  resume (AI copywriting) — this project deliberately has no LLM
  dependency for this; say plainly that tailoring here means
  reordering/emphasizing existing content, not rewriting it.
- Explaining a score itself (→ job-matching).

## Inputs

`candidate_id`; for upload, a local `.pdf` file path; for pinning, a
`search_id` and the target `resume_id`/profile version; for tailoring,
a `job_id` and the `resume_id` to tailor from.

## Workflow

1. **List resumes**: `GET /api/candidates/{id}/resumes` — filename,
   version label, upload date, status, `resume_id`. Every version is
   kept; uploading a new one never deletes or overwrites an older one.
2. **Upload**: `POST /api/candidates/{id}/resume` as multipart form
   data, field name `file`, `.pdf` only (server validates the PDF
   magic bytes and a size limit — do not attempt a non-PDF). Example:
   `curl -F file=@/path/to/resume.pdf http://127.0.0.1:8420/api/candidates/{id}/resume`.
   This creates a new DRAFT profile — the user must still review and
   confirm it at `/profile` (or `POST /api/candidates/{id}/profile/confirm`)
   before it can be used for a search.
3. **Which resume scored a job**: already present on each result from
   `GET /api/searches/{search_id}/results` (`resume_variant`/
   `resume_id` fields) — read it from there, never guess.
4. **Pin a search to a specific resume/profile version**:
   `GET /api/candidates/{id}/profile-versions` to list versions, then
   set the search's pinned version via the search create/edit payload
   (`PUT/PATCH /api/searches/{search_id}`). An old search stays tied to
   whatever it was pinned to even after a newer resume is uploaded;
   only a future run of a search picks up "current profile" if that's
   what it's pinned to.
5. **Tailor a resume for a job**: confirm the profile is `CONFIRMED`
   (`GET /api/candidates/{id}/profile`), then
   `POST /api/candidates/{id}/jobs/{job_id}/tailor-resume` with
   `{"base_resume_id": ..., "job_id": ...}`. The backend independently
   re-checks eligibility (same gate as scoring) — a 404 here means this
   candidate isn't eligible for that job, not a bug to work around.
   Each call creates a NEW, immutable `tailoring_version` (never
   overwrites an earlier one, never touches the original uploaded
   resume). Report the version number, `factual_safety_status`, and
   `keywords_emphasized` from the real response.
6. **List/download tailored versions**:
   `GET /api/candidates/{id}/jobs/{job_id}/tailored-resumes` (all
   versions), `GET .../tailored-resumes/{id}/download` (plain-text
   export — there is no PDF-generation library in this project, so the
   export is deliberately plain text, not a new PDF).

## APIs / Tools

`GET /api/candidates/{id}/resumes`, `POST /api/candidates/{id}/resume`,
`GET /api/candidates/{id}/profile-versions`,
`PUT/PATCH /api/searches/{search_id}`,
`POST /api/candidates/{id}/jobs/{job_id}/tailor-resume`,
`GET /api/candidates/{id}/jobs/{job_id}/tailored-resumes`,
`GET /api/candidates/{id}/tailored-resumes/{id}`,
`GET /api/candidates/{id}/tailored-resumes/{id}/download`.

## Safety

- Never invents resume content, in either "which resume" selection or
  tailoring. Tailoring only ever reorders/extracts text that already
  exists in the candidate's own confirmed profile — never a rewritten
  sentence, never an added skill/employer/certification/metric.
- If asked for genuine AI rewriting/copywriting, say plainly that this
  project has no LLM dependency and tailoring here means deterministic
  reordering/emphasis, not rewriting.
- Never uploads a file the user didn't explicitly provide.
- Never marks a DRAFT profile as confirmed on the user's behalf.
- If `factual_safety_status` comes back `REJECTED` on a real response
  (should be structurally rare), report it honestly rather than
  presenting the content as safe.

## Output

The real list of resumes/versions with their IDs and dates, confirmation
of an upload's resulting `resume_id` and DRAFT status, or a tailored
resume's real version/status/factual-safety-status/keywords from the
API response.

## Examples

- "What resumes do I have?" → the real list from the API.
- "Upload my new resume at ~/Downloads/resume_v2.pdf." → upload, report
  the new `resume_id`, remind the user to confirm the resulting profile.
- "Which resume scored the Akamai SRE job?" → read `resume_variant`
  from that specific result.
- "Tailor my resume for this job." → confirm profile is CONFIRMED, call
  tailor-resume, report the real version/status/factual-safety result.
  Never describe this as "rewriting" or "AI-generated."
