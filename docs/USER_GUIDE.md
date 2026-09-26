# User Guide

Organized around what you're actually trying to do. See
[GETTING_STARTED.md](GETTING_STARTED.md) first if you haven't installed
JobOS yet.

## "I want to find jobs."

Create a search at `/searches/new` with your target roles/locations,
pick from the sources shown as available, and click "Run now" (or ask
Claude — see [CLAUDE_COOKBOOK.md](CLAUDE_COOKBOOK.md)). Results appear
at `/searches/{id}/results`.

## "I want to find jobs matching my profile."

That's what every search does by default — 6 of the 10 scoring
dimensions compare the job against **your own** confirmed profile
(target roles, cloud/Kubernetes/IaC/CI-CD/observability skills), not a
generic checklist. See [SCORING.md](SCORING.md).

## "I want AWS jobs." / "I want SRE jobs."

List the role/skill in your search criteria and/or your profile's
target roles and skills — the scoring engine reads directly from what
you entered, nothing is hardcoded to any particular profession.

## "I want to understand a match score."

Click any result for its detail view: the raw score, priority tier
(A/B/C), and the exact "Matched Skills" / "Gaps" breakdown that
produced it. Full explanation of every dimension in
[SCORING.md](SCORING.md). Ask Claude "explain why this job scored X"
for the same data in plain language.

## "I want to identify skill gaps."

The same detail view's "Gaps" list — dimensions where the job wanted
something your profile doesn't show. Never inferred beyond what's
explicitly missing from your own listed skills.

## "I want to choose a resume."

The Profile page lists every resume you've ever uploaded. When
creating/editing a search, the "Resume/Profile version" selector lets
you pin it to a specific past resume instead of always using whichever
is current. See "Multiple resumes" in the main [README](../README.md).

## "I want to track an application."

Use the status dropdown on a result's detail view (or
`PATCH /api/candidates/{id}/jobs/{job_id}/status`): Shortlist → Approve
→ Applied, etc. The one rule: you can't reach Applied without having
set Approved first. Nothing here ever auto-applies — see
[APPLICATION_LIFECYCLE.md](APPLICATION_LIFECYCLE.md).

## "I want to see pending follow-ups."

`/dashboard` shows your candidate-scoped pipeline and recent activity.
There's no dedicated "follow-up date" feature wired up yet (the field
exists in the schema but no code reads/writes it today) — track
follow-ups by status/history for now.

## "I want to use Claude."

Just ask, in plain language, once the app is running — "find AWS
Senior SRE jobs in Bangalore," "show my pipeline," etc. See
[CLAUDE_GUIDE.md](CLAUDE_GUIDE.md).

## "I want to automate recurring searches."

**Planned/Future, not yet built for the current multi-candidate app.**
A `launchd` template exists for the older, legacy single-candidate CLI
pipeline (`launchd/com.sarojjobos.dailysearch.plist`), deliberately
**not installed** by default — installing it is a manual, human
decision. There is no scheduled-run feature in the current web app/API
today. Re-running a saved search on demand (via UI or Claude) is fully
supported; recurring/scheduled runs are not.
