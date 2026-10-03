# First Search Walkthrough

A complete, concrete walkthrough using a fictional candidate — **Alex
Doe**, a Senior Site Reliability Engineer targeting Bengaluru,
Hyderabad, and Remote India roles, with skills in Kubernetes, AWS,
Azure, Terraform, Python, and Prometheus. None of this is real data —
every value below is a placeholder you replace with your own.

This is executable against a genuinely fresh clone — nothing below
assumes any prior setup.

## 1-3. Clone, install, start

```bash
git clone <this-repo-url>
cd SarojJobOS
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm install
npx playwright install chromium
.venv/bin/uvicorn api.main:app --reload --port 8420
```
(Windows: see [GETTING_STARTED.md](GETTING_STARTED.md) for the
PowerShell equivalents.) Open **http://127.0.0.1:8420/**.

## 4. Create candidate

On first visit, choose **"Enter profile manually"** (or upload a
resume — step 5 covers that path). Enter:
- Name: `Alex Doe`
- Email: `alex@example.com`

JobOS creates a candidate record with a server-generated ID (e.g.
`cand_7f3a9c2b1d84`) — you never choose this yourself.

## 5. Upload resume

Instead of (or in addition to) manual entry, go to the Profile page
and upload a `.pdf` resume. For this walkthrough, imagine Alex's
resume states: *"Senior Site Reliability Engineer with 8 years of
experience. Skills: Kubernetes, AWS, Azure, Terraform, Python,
Prometheus."*

## 6. Review extraction

JobOS extracts a DRAFT profile: name "Alex Doe," 8 years experience
(because the resume literally states "8 years of experience" — never
computed by summing dates), and the listed skills bucketed into
categories (Kubernetes → containers/orchestration, AWS/Azure → cloud,
Terraform → IaC, Prometheus → observability). Anything the resume
didn't literally state is left blank — never guessed.

## 7. Edit profile

On the Profile page, add anything missing — for Alex, set **target
locations**: `Bengaluru, Hyderabad, Remote`.

## 8. Confirm profile

Click **"Confirm profile."** This is required — **no search can run
against an unconfirmed profile.**

## 9-14. Provider account, key, Settings, add, test, enable

JobOS works right now with zero provider keys — four boards (Naukri,
Hirist, IIMJobs, Apna) need nothing. To also unlock 7 more boards
(LinkedIn, Indeed, Foundit, Instahyre, Cutshort, Wellfound, Shine):

1. Create a free account on, e.g., **Tavily** (https://tavily.com).
2. Generate your own API key in their dashboard.
3. In JobOS, go to **Settings → Search Providers**.
4. Paste your key into the Tavily field and save — JobOS shows it back
   masked (`••••••••1234`), never in full.
5. Click **Test** — confirms the key actually works.
6. Click **Enable.**

(Full provider-by-provider detail:
[API_PROVIDER_SETUP.md](API_PROVIDER_SETUP.md). Skip this entirely if
you're fine with just the 4 direct boards.)

## 15. Verify sources

`/api/sources` (or the Sources view on the search-creation page) now
shows the 7 provider-backed boards as `AVAILABLE_VIA_SEARCH_PROVIDER`
instead of `SEARCH_PROVIDER_NOT_CONFIGURED`.

## 16. Create search

`/searches/new`:
- Search name: `Bengaluru SRE roles`
- Job title/keywords: `Senior SRE, Platform Engineer`
- Locations: `Bengaluru, Hyderabad, Remote`
- Skills/criteria: `Kubernetes, AWS, Terraform`
- Minimum match score: `70`
- Sources: select whichever are available (step 15)

## 17. Run search

Click **"Run now."** The page polls until the run finishes.

## 18. Review source audit

The results page shows each selected source's real outcome —
`SUCCESS`, `ZERO`, `FAILED`, `BLOCKED`, `NOT_CONFIGURED`, or
`NOT_ATTEMPTED`. See [SEARCH.md](SEARCH.md) for what each means.

## 19. Review score

Click into a result for its breakdown — matched skills, gaps, and the
raw 0-100 score. See [MATCHING.md](MATCHING.md) for how it's computed.

## 20. Open job

The job workspace page — the hub for research, tailoring, and prep for
this one job.

## 21. Shortlist

Status dropdown → **Shortlisted.**

## 22. Company research

Click **"Research Company."** Confirm the company name if asked — this
runs a real search via your configured provider (or reports
`NOT_ATTEMPTED` if you skipped steps 9-14).

## 23. Resume selection

If you have multiple uploaded resumes, pick which one this application
should use from the resume selector.

## 24. Resume tailoring (deterministic, not AI)

Click **"Tailor Resume."** This reorders Alex's own existing summary
sentences, skills, and bullet points so the ones matching this job
come first — **it never invents a skill, employer, or achievement Alex
doesn't actually have.**

## 25. Interview preparation

Click **"Generate Interview Preparation."** Produces category-grouped
questions (Kubernetes, Cloud, Terraform, CI/CD, Observability, etc.),
each with either a real quote from Alex's resume as a suggested
answer, or an honest "Preparation required" where no matching
experience exists.

## 26. Approve

Status dropdown → **Approved.** (Required before you can later mark it
Applied.)

## 27. Apply manually

**Go apply on the employer's own website yourself.** JobOS never
submits this for you.

## 28. Mark applied

Back in JobOS, click **"Mark as Applied."** Confirm which resume
version you actually used — this records `applied_at` once, honestly.

## 29. Add notes

On the same application, add a free-text note — e.g. *"Applied via
company careers page, referred by a contact."*

## 30. Schedule follow-up

**"Schedule follow-up"** → pick a due date (e.g. 7 days out) → note:
*"Follow up with recruiter if no response."*

## 31. Prepare for interview

If/when an interview is scheduled, revisit the Interview Preparation
from step 25 and review your saved answers.

## 32. Record interview outcome

After the interview, record the real outcome (Completed, Passed,
Rejected, No Response, etc.).

## 33. Complete follow-up

Once you've actually followed up, click **"Follow Up Now"** on that
follow-up — marks it done, keeps the full history, never deletes it.

---

That's the complete lifecycle, start to finish, using only fictional
data. See [USER_GUIDE.md](USER_GUIDE.md) for the same features
explained screen-by-screen, or [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
for a one-page cheat sheet once you know your way around.
