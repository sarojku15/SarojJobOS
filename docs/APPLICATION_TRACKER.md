# Application Tracker

**JobOS does NOT automatically submit job applications — ever.** You
apply on the employer's own site yourself, then come back and tell
JobOS what you did. Every status below is something *you* told JobOS,
never something JobOS did on its own.

The full, authoritative 18-state lifecycle and its rules live in
[APPLICATION_LIFECYCLE.md](APPLICATION_LIFECYCLE.md) — this page is
the practical "how do I actually use it" companion.

## The core flow

```
Discovered (a search found it)
   ↓
Shortlisted  (you: status dropdown or job workspace)
   ↓
Ready for Approval
   ↓
Approved  (you)
   ↓
Application Started  (optional intermediate state)
   ↓
Applied  ← you apply on the employer's site FIRST, then record this
   ↓
Interview stages (Recruiter Contacted, Screening Call, Interview 1/2, Final Round)
   ↓
Terminal states (Offer, Employer Rejected, Withdrawn, Ghosted, ...)
```

## The approval gate

**You cannot reach "Applied" without first setting "Approved."** This
is enforced server-side (not just a UI suggestion) — an attempt to
jump straight to Applied is rejected.

## Marking applied

Once you've actually applied on the employer's site, use the dedicated
**"Mark as Applied"** action (job workspace, or from "My
Applications") rather than just picking "Applied" from the status
dropdown. This also:
- records `applied_at` — set exactly once, the first time, never
  overwritten by a later status change
- lets you confirm **which resume you actually used** (your match-time
  resume is suggested as a default; you always confirm it)

## Notes

Add/edit a free-text note on any application at any time — purely
yours, never generated.

## Interview outcome

Recording a real interview outcome (see
[INTERVIEW_PREP.md](INTERVIEW_PREP.md)) can automatically advance the
main application status for the two unambiguous terminal outcomes
(Rejected → Employer Rejected, No Response → Ghosted) — but only while
the status is still genuinely "in flight," never overwriting a status
that's already moved past that point (e.g. already Offer, already
Withdrawn).

## Follow-ups

See [FOLLOWUPS.md](FOLLOWUPS.md) — set from the same application.

## History

Every status transition is recorded, in order, with a timestamp — `GET
/api/candidates/{id}/jobs/{job_id}/status-history`. Nothing is ever
silently overwritten in this history.

## "My Applications"

`/applications` — every job you've shortlisted, approved, or applied
to, **across all your saved searches**, not scoped to one search's
results page. Filter by status/company/source/follow-up state; summary
cards show your real totals.
