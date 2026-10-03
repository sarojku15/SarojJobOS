# Resumes

## Upload

**Profile** page → upload a `.pdf`. JobOS checks the actual file
bytes (not just the filename) to confirm it's a real PDF, and enforces
a size limit. Each upload is stored under a server-generated name —
your original filename is kept as metadata only.

## Extraction

Uploading triggers automatic extraction into a new **DRAFT** profile
version (`scripts/resume_extractor.py`): identity, a stated years-of-
experience figure (only if your resume literally says "N years of
experience" — never computed by summing employment date ranges),
skills bucketed into categories (cloud, containers/orchestration, IaC,
CI/CD, observability, etc.), employment history, certifications, and
education. **Nothing is invented** — a field with no literal textual
basis in your resume is left empty, never guessed.

## Reviewing and correcting

After upload, review the extracted draft on the Profile page. Fix
anything the extractor got wrong or missed via `PUT
/api/candidates/{id}/profile` (the Profile page's edit form) — this is
a normal, expected step, not a failure mode.

## Confirming

**Confirm profile** promotes the current DRAFT to **CONFIRMED**.
**A search cannot run until this has happened at least once.** This
exists so scoring never runs against an unreviewed, possibly-wrong
extraction.

## Multiple resumes

Upload as many resumes as you want. **None is ever deleted or
silently overwritten** — every upload creates a new, independently
retrievable resume record and (if it parses successfully) a new
profile version. See them all on the Profile page or via `GET
/api/candidates/{id}/resumes`.

## Resume/profile version pinning

When creating or editing a search, the **"Resume / Profile version"**
field lets you pin that search to a specific past resume/profile
version instead of always scoring against whichever is current. This
is useful if you want to compare how different resume versions would
have scored the same jobs.

## Resume tailoring

**Deterministic, rule-based — not AI, not an LLM.** From a job's
workspace page, pick one of your uploaded resumes and click **"Tailor
Resume."** This reorders content that already exists in your profile
so job-relevant material appears first:
- your summary's own sentences, reordered (none rewritten, none added)
- your own skill names, reordered so ones the job actually asked for
  come first
- your own employment-history bullet points, extracted into a
  "relevant highlights" view where their listed skills overlap the job

**It never invents an employer, project, certification, year of
experience, technology, title, achievement, or metric.** An automated
factual-safety check runs before anything is saved, confirming the
tailored output only contains content traceable to your own profile.
Only available for a job you're actually eligible for (same gate as
scoring).

## Generated variants and storage

Every tailoring attempt creates a new, numbered, immutable version —
your original resume and every earlier tailored version are never
overwritten. List them via `GET .../tailored-resumes`; download one as
plain text via `GET .../tailored-resumes/{id}/download`.

## What's never implemented

- No AI/LLM resume generation or rewriting of any kind.
- No cover-letter generation.
- No automatic submission of a tailored resume to an employer — you
  download it and use it yourself.
