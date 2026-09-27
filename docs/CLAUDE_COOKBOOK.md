# Claude Cookbook

Natural-language prompts that work against the current JobOS system
(see [CLAUDE_GUIDE.md](CLAUDE_GUIDE.md) for how routing works). All of
these assume the app is running and Claude knows your `candidate_id`
for the session.

### Job Discovery

- "Find AWS Senior SRE jobs in Bangalore."
- "Run my current job searches."
- "Search for Platform Engineer roles in Hyderabad, remote-friendly."
- "Which sources actually ran last time, and which were skipped?"

### Job Matching

- "Find jobs scoring above 80."
- "Explain why these jobs match me."
- "Show me skill gaps for this job."
- "Which of these results is the strongest match?"
- "Compare this job against the last one you showed me."

### Resume

- "What resumes do I have uploaded?"
- "Upload my new resume at ~/Downloads/resume_v2.pdf."
- "Which resume was used to score this job?"
- "Pin this search to my previous resume instead of the current one."
- "Tailor my resume for this job." (deterministic reorder/emphasis of
  your own existing content — never AI rewriting; only for a job
  you're actually eligible for)
- "Show me the previous tailored versions for this job."

### Application Tracking

- "Show my current application pipeline."
- "Show applications needing follow-up."
- "Mark this job as shortlisted."
- "I just got approved to apply for this one — mark it approved."
- "I applied to this job yesterday, record that."
- "What's the full status history for this job?"
- "Remind me to follow up on this job next Friday." (sets the real
  candidate+job-scoped follow-up date; never changes application status)

### Company Research

- "Research this company before I apply." (real, persisted, source-
  backed — via `company-research`; honest `NOT_ATTEMPTED` if no
  search-provider key is configured)
- "Refresh the research on this company."
- "What sources back up that description?"

### Interview Preparation

- "Prepare me for the interview for this job." (real, deterministic,
  evidence-based — via `interview-prep`; grounded in your own confirmed
  profile/resume, plus linked tailored resume/company research if any)
- "What should I say if they ask about Terraform?" (if you have no
  demonstrated Terraform experience, expect an honest "Preparation
  required" note, never a fabricated anecdote)
- "Save my answer to the Kubernetes question."
- "I passed the interview, mark it."

### Reporting

- "Generate today's job-search report."
- "Download the Excel report for this search."
- "What fields does the export actually include?"

### Scheduling

- "Schedule this search to run every morning." → sets a real, persisted
  daily schedule (`PUT /api/searches/{id}/schedule`) — Claude should be
  explicit that this alone doesn't start anything; an external trigger
  (the `JobOS Scheduled Search Runner` n8n workflow, or
  `scripts/run_scheduled_searches.py` on cron/launchd) must actually be
  running for it to fire.
- "Is this search scheduled? When does it run next?" →
  `GET /api/searches/{id}/schedule`, report the real
  `next_run_at`/`last_run_at`/`last_run_status`.
- "Turn off scheduling for this search."

### Automation

- "Prepare an application checklist for this job." (routes through
  `jobos-orchestrator`'s "prepare this job" workflow: score + gaps +
  resume in use, summarized — stops at information, never proceeds to
  an application-status write without you saying you've already acted)
- "Is the scheduled daily search set up?" — check the real schedule
  state (above) rather than assuming; the legacy CLI pipeline's
  separate `launchd` template
  (`launchd/com.sarojjobos.dailysearch.plist`) remains deliberately
  **not installed** — installing anything is a human-approved step,
  never something Claude does on its own initiative.
