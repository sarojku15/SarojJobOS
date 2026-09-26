# Glossary

Plain-language definitions of terms used across JobOS, its API, and its
documentation. Written for a non-developer reading their own results.

| Term | Meaning |
|---|---|
| **Candidate** | One person's independent JobOS account: their own profile, resumes, searches, results, and application statuses. Identified by a `candidate_id`. A fresh install has zero candidates. |
| **Profile** | A candidate's structured data: identity (name/email/phone), skills, total experience, and job preferences (target roles/locations). Built from an uploaded resume, manual entry, or both. Must be explicitly **confirmed** before any search can run. |
| **Resume Version** | One uploaded resume file. Every candidate can upload multiple; none are ever deleted or overwritten. Each has a `resume_id`, filename, upload date, and status (e.g. `PARSED`). Searches can be pinned to a specific resume version instead of "whichever is current." |
| **Search (Saved Search)** | A named, reusable set of search criteria (job titles, locations, experience/salary range, skills, work model, minimum score, sources to query). Has a `saved_search_id`. Running it produces a **Search Run**. |
| **Search Run** | One execution of a saved search at a point in time. Has a `run_id`, a status (`QUEUED` → `RUNNING` → `COMPLETED`/`PARTIAL`/`FAILED`/`BLOCKED`), and its own scoped set of results and per-source audit rows. Re-running the same search creates a new run; past runs' results are never overwritten. |
| **Job** | One discovered job posting, normalized into a common shape (title, company, location, JD text, URL, etc.) regardless of which source it came from. Deduplicated across sources where possible. |
| **Source** | A specific job board or discovery channel (e.g. `NAUKRI`, `LINKEDIN`). See [JOB_SOURCES.md](JOB_SOURCES.md) for the full current list and each one's real status. |
| **Provider** | A third-party search API (You.com, Tavily, Exa, Brave, or Serper) used to discover jobs on boards JobOS does not crawl directly (LinkedIn, Indeed, Foundit, Instahyre, Cutshort, Wellfound, Shine). |
| **Eligibility** | A hard, binary gate (experience range + location) run before scoring. An ineligible job is never scored or shown as a match, regardless of how well its text matches — see [SCORING.md](SCORING.md). |
| **Match Score** | The 0–100 point score a job receives against **your own** profile (`scripts/score_job.py`). Higher is a stronger textual/skills match, mapped to a priority tier (A/B/C/Reject). It is a decision-support signal, not a guarantee of interview or hiring outcome. |
| **Skill Gap** | A scoring dimension where the job asked for something your profile doesn't show (e.g. "Terraform/IaC" listed under `missing_skills`). Never fabricated — if your profile doesn't list a skill, it shows as a gap rather than being silently assumed. |
| **Shortlist** | The first step of the (fully manual, human-driven) application-tracking lifecycle: marking a job you're interested in pursuing. See [APPLICATION_LIFECYCLE.md](APPLICATION_LIFECYCLE.md). |
| **Application** | A record of *you* applying to a job outside JobOS (there is no auto-apply). JobOS only records the status you tell it, after you've actually taken that action yourself. |
| **Application Status** | One of the 18 states in `config/application_schema.json` (e.g. `FOUND`, `SHORTLISTED`, `APPROVED`, `APPLIED`, `INTERVIEW_1`, `OFFER`). Transitions are enforced server-side — most importantly, `APPLICATION_STARTED`/`APPLIED` are unreachable until you've explicitly set `APPROVED` first. |
| **Source Audit** | The per-run, per-source record of what actually happened when a search ran: `SUCCESS`, `ZERO` (ran fine, found nothing), `FAILED`, `BLOCKED`, `NOT_CONFIGURED`, or `NOT_ATTEMPTED` — never silently hidden or misrepresented as a different status. Visible on every results page. |
