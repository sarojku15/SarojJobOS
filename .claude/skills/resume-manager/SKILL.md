---
name: resume-manager
description: List, upload, and explain resume/profile version selection for the current JobOS candidate — which resumes exist, which one scored a given result, and how to pin a search to a specific resume/profile version. Use when the user asks about their resumes, resume versions, or which resume was used for a job/search.
---

# Resume Manager

Wraps the real resume-storage and profile-versioning system
(`scripts/resume_store.py`, `scripts/candidate_profile.py`) — it does
not tailor, rewrite, or generate resume content (that capability does
not exist in this project; see Safety).

## When to use

- "What resumes do I have uploaded?"
- "Upload this resume."
- "Which resume was used to score this job?"
- "Pin this search to my old resume instead of my current one."

## When not to use

- Generating or rewriting resume *content* for a specific job — not
  implemented; say so plainly rather than attempting it (see Safety).
- Explaining a score itself (→ job-matching).

## Inputs

`candidate_id`; for upload, a local `.pdf` file path; for pinning, a
`search_id` and the target `resume_id`/profile version.

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

## APIs / Tools

`GET /api/candidates/{id}/resumes`, `POST /api/candidates/{id}/resume`,
`GET /api/candidates/{id}/profile-versions`,
`PUT/PATCH /api/searches/{search_id}`.

## Safety

- Never invents resume content. "Resume variant selection" in this
  project means *which of the user's own uploaded resumes* was used —
  never rewriting or generating new resume text for a specific job.
  If asked to tailor a resume's actual content, say plainly that this
  capability is not implemented (Planned/Future), rather than
  fabricating a rewrite.
- Never uploads a file the user didn't explicitly provide.
- Never marks a DRAFT profile as confirmed on the user's behalf.

## Output

The real list of resumes/versions with their IDs and dates, or
confirmation of an upload's resulting `resume_id` and DRAFT status.

## Examples

- "What resumes do I have?" → the real list from the API.
- "Upload my new resume at ~/Downloads/resume_v2.pdf." → upload, report
  the new `resume_id`, remind the user to confirm the resulting profile.
- "Which resume scored the Akamai SRE job?" → read `resume_variant`
  from that specific result.
