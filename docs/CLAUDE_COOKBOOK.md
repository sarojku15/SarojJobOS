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

### Application Tracking

- "Show my current application pipeline."
- "Show applications needing follow-up."
- "Mark this job as shortlisted."
- "I just got approved to apply for this one — mark it approved."
- "I applied to this job yesterday, record that."
- "What's the full status history for this job?"

### Company Research

- "Tell me about this company before I apply." (best-effort, informal —
  see `company-research`'s SKILL.md for its honest limitations)

### Interview Preparation

Not a built JobOS capability today — see
[USER_GUIDE.md](USER_GUIDE.md)'s "Planned/Future" notes. Claude can
still help conversationally using the job's real JD text and your real
profile (never fabricating either), but there is no dedicated skill or
API for this yet.

### Reporting

- "Generate today's job-search report."
- "Download the Excel report for this search."
- "What fields does the export actually include?"

### Automation

- "Prepare an application checklist for this job." (routes through
  `jobos-orchestrator`'s "prepare this job" workflow: score + gaps +
  resume in use, summarized — stops at information, never proceeds to
  an application-status write without you saying you've already acted)
- "Is the scheduled daily search set up?" — honest answer: the
  `launchd` template exists (`launchd/com.sarojjobos.dailysearch.plist`)
  but is deliberately **not installed** (see `docs/ARCHITECTURE.md`);
  installing it is a separate, human-approved step, not something
  Claude does on its own initiative.
