# Follow-Ups

## Creating one

From a job's application view (job workspace or "My Applications"):
**Schedule** (or **Reschedule**) a follow-up with a due date and an
optional note. Examples of what you might use the note for:
- "Follow up with recruiter"
- "Check application status"
- "Send interview thank-you"

Only the actions below are actually supported — nothing here sends an
email or message for you; it's a reminder *you* act on.

## Due / overdue

Each follow-up carries one server-computed state — `is_overdue`,
`is_due_today`, `is_due` (overdue or due today), `is_upcoming` — so the
dashboard, the follow-up list, and the n8n reminder workflow can never
disagree about whether something is due.

## Completing

**"Follow Up Now"** marks the current follow-up **completed** (with a
real `completed_at` timestamp) — it does not delete the record.
Scheduling your next follow-up is a separate, subsequent action.

## Cancelling

**Cancel** marks it cancelled without claiming it was completed — a
distinct terminal state, so your history honestly shows "I decided not
to" versus "I actually did this."

## History

Every follow-up instance you've ever scheduled/completed/cancelled for
a job is kept — `GET .../jobs/{job_id}/follow-up-history`. Nothing is
ever deleted.

## Dashboard visibility

The dashboard's Follow-ups card lists every active follow-up, soonest/
most-overdue first, each clearly marked Overdue/Due today/Upcoming.

## n8n reminder

The optional **JobOS Follow-Up Reminder** n8n workflow calls `GET
/api/candidates/<your-candidate-id>/follow-ups?due_only=true` once a
day and logs each due/overdue item to n8n's own Executions tab — it is
read-only and never writes anything back to JobOS. See
[N8N_SETUP.md](N8N_SETUP.md) for the full setup, and
[AUTOMATION.md](AUTOMATION.md) for exactly what triggers it and how to
disable it.

## When nothing is due

The follow-ups list/dashboard card is simply empty, and the n8n
reminder's response is `{"follow_ups":[]}` — a normal, expected state,
not an error.
