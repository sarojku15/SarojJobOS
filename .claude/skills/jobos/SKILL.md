---
name: jobos
description: Use this skill when the user invokes /jobos, asks to "search for jobs", "check job status", "add a job", "generate a job report", "process job input", "show pending jobs", or anything related to the SarojJobOS job-search automation system. Sub-commands are search, status, report, add, process, and help.
disable-model-invocation: true
allowed-tools: Read, Bash
---

# SarojJobOS — /jobos Skill

**This drives the legacy, single-candidate CLI pipeline**
(`config/profile.json`, `data/inbox/job_input.txt`,
`scripts/process_job_input.py`, `scripts/generate_daily_report.py`) —
a separate, older code path from the current multi-candidate web app/
API (`api/`, `web/`) that most users interact with. For the current
system, use the `jobos-orchestrator` skill instead (auto-invoked by
natural language, no slash command needed). See
`docs/ARCHITECTURE.md`'s "Two coexisting pipelines" section for how the
two relate. This skill is kept because it's still real, tested,
working functionality for whoever set up the CLI pipeline originally —
not because it's the recommended entry point for a new user.

You are the conversational orchestration layer for this legacy pipeline.
You do not contain a scoring engine, a database, or a tracking system.
Those already exist in the Python pipeline. Your only job is to route
user commands to the correct existing scripts, present their output
clearly, and enforce the safety rules defined in CLAUDE.md.

## Live System State

- DB record count: !`sqlite3 data/applications/jobos.db "SELECT COUNT(*) || ' total jobs (' || SUM(CASE WHEN source NOT IN ('TEST','MOCK') THEN 1 ELSE 0 END) || ' real, ' || SUM(CASE WHEN source IN ('TEST','MOCK') THEN 1 ELSE 0 END) || ' test/mock)' FROM jobs;" 2>/dev/null || echo "DB not accessible"`
- Priority breakdown: !`sqlite3 data/applications/jobos.db "SELECT priority || ': ' || COUNT(*) FROM jobs WHERE source NOT IN ('TEST','MOCK') GROUP BY priority ORDER BY priority;" 2>/dev/null || echo ""`
- Status breakdown: !`sqlite3 data/applications/jobos.db "SELECT status || ': ' || COUNT(*) FROM jobs WHERE source NOT IN ('TEST','MOCK') GROUP BY status ORDER BY COUNT(*) DESC LIMIT 8;" 2>/dev/null || echo ""`

## Arguments

The user invoked this with: $ARGUMENTS

Parse $ARGUMENTS to determine the sub-command. If no argument is given,
show the help text below.

---

## Sub-commands

### `/jobos help`

Print this usage guide:

```
SarojJobOS — Job Search OS for Saroj Kumar Nayak

Usage:
  /jobos help       Show this guide
  /jobos status     Show DB summary and pipeline health
  /jobos report     Generate today's daily report (HTML + CSV)
  /jobos add        Guide for adding a job via job_input.txt
  /jobos process    Parse job_input.txt → score → ingest → report
  /jobos search     Show query plan (4 direct sources live; 7 more via search provider when configured)
  /jobos tests      Run the full test suite

Candidate: (read live from config/profile.json -- run the command
            below rather than assuming a name)
Scoring:   100-point engine (A≥90, B≥80, C≥70, Reject<70)
Tracker:   data/applications/jobos.db (SQLite — system of record)
```

Then actually run this and substitute the real values into "Candidate:"
above rather than a hardcoded name -- this file is shared, and whoever
set up this legacy pipeline may not be Saroj:
```bash
python3 -c "
import json
p = json.load(open('config/profile.json'))
c = p.get('candidate', {})
print(f\"{c.get('name', 'UNKNOWN')} — {c.get('experience_years', '?')}yr {c.get('current_title', '')}\")
"
```

---

### `/jobos status`

Run the following and present results:

```bash
python3 -c "
import sqlite3, json
from pathlib import Path

db = Path('data/applications/jobos.db')
if not db.exists():
    print('ERROR: jobos.db not found. Run: python3 scripts/init_tracker.py')
    exit(1)

conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

total = conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
real  = conn.execute(\"SELECT COUNT(*) FROM jobs WHERE source NOT IN ('TEST','MOCK')\").fetchone()[0]
mock  = conn.execute(\"SELECT COUNT(*) FROM jobs WHERE source IN ('TEST','MOCK')\").fetchone()[0]

print(f'Total jobs     : {total}')
print(f'Real jobs      : {real}')
print(f'Test/mock jobs : {mock}')
print()

rows = conn.execute(\"\"\"
    SELECT priority, COUNT(*) as n
    FROM jobs WHERE source NOT IN ('TEST','MOCK')
    GROUP BY priority ORDER BY priority
\"\"\").fetchall()
if rows:
    print('Priority breakdown (real jobs):')
    for r in rows:
        print(f'  {r[\"priority\"] or \"—\"}: {r[\"n\"]}')
    print()

rows = conn.execute(\"\"\"
    SELECT status, COUNT(*) as n
    FROM jobs WHERE source NOT IN ('TEST','MOCK')
    GROUP BY status ORDER BY n DESC LIMIT 10
\"\"\").fetchall()
if rows:
    print('Status breakdown (real jobs):')
    for r in rows:
        print(f'  {r[\"status\"]}: {r[\"n\"]}')
    print()

latest = conn.execute(\"\"\"
    SELECT source, company, title, score, priority, status, last_updated
    FROM jobs
    WHERE source NOT IN ('TEST','MOCK')
    ORDER BY last_updated DESC
    LIMIT 5
\"\"\").fetchall()
if latest:
    print('5 most recently updated real jobs:')
    for r in latest:
        print(f'  {r[\"source\"]} | {r[\"company\"]} | {r[\"title\"]} | {r[\"score\"]} | {r[\"priority\"]} | {r[\"status\"]}')
else:
    print('No real jobs in tracker yet.')
    print('Add jobs via: /jobos add  or  /jobos process')

conn.close()
"
```

After presenting the output, if real job count is 0, suggest:
"No real jobs are tracked yet. Use `/jobos add` to add your first real job,
or `/jobos process` if you've already filled out `data/inbox/job_input.txt`."

---

### `/jobos report`

Run:
```bash
python3 scripts/generate_daily_report.py
```

Then run:
```bash
ls -lh data/reports/ | tail -10
```

Present the output. Tell the user:
- The exact paths of the HTML and CSV files generated
- The count of qualifying jobs (score ≥ 70, not TEST/MOCK)
- How to open the HTML report: `open data/reports/<filename>.html`

Do not generate or invent job content. Present only what the script outputs.

---

### `/jobos add`

This guides the user to manually add a real job they found on a job board.

Print the following instructions:

```
HOW TO ADD A JOB

Option 1 — Paste-in workflow (recommended for a single job):

1. Open:  data/inbox/job_input.txt
2. Fill in the fields at the top:
     SOURCE:     (NAUKRI / LINKEDIN / INDEED / COMPANY / etc.)
     URL:        (paste the full job URL)
     Company:    
     Job Title:  
     Location:   
     Experience: 
     Work Model: (Remote / Hybrid / WFO)
     Posted Date:
3. Paste the COMPLETE job description text below the marker line.
4. Then run: /jobos process

Option 2 — Interactive CLI (adds to batch inbox):

Run: python3 scripts/add_job.py
Then run: python3 scripts/ingest_jobs.py data/inbox/jobs.json

Current job_input.txt status:
```

Then run:
```bash
python3 -c "
from pathlib import Path
f = Path('data/inbox/job_input.txt')
if not f.exists():
    print('  File not found.')
else:
    text = f.read_text()
    has_url  = bool([l for l in text.splitlines() if l.strip().startswith('URL:') and len(l.strip()) > 4])
    has_jd   = 'PASTE THE COMPLETE JOB DESCRIPTION BELOW THIS LINE' in text
    after_jd = text.split('PASTE THE COMPLETE JOB DESCRIPTION BELOW THIS LINE')[-1].strip() if has_jd else ''
    print(f'  URL field filled : {has_url}')
    print(f'  JD marker found  : {has_jd}')
    print(f'  JD content chars : {len(after_jd)}')
    if len(after_jd) > 20:
        print('  Status           : READY — run /jobos process')
    else:
        print('  Status           : EMPTY — fill in job details first')
"
```

---

### `/jobos process`

This runs the full 3-step pipeline: parse → score/ingest → report.

First verify the input file has content:
```bash
python3 -c "
from pathlib import Path
f = Path('data/inbox/job_input.txt')
if not f.exists():
    print('ERROR: data/inbox/job_input.txt not found')
    exit(1)
text = f.read_text()
marker = 'PASTE THE COMPLETE JOB DESCRIPTION BELOW THIS LINE'
after = text.split(marker)[-1].strip() if marker in text else ''
if len(after) < 20:
    print('ERROR: Job description is empty or too short.')
    print('Fill in data/inbox/job_input.txt first, then run /jobos process again.')
    exit(1)
print('OK: Input file has content. Proceeding...')
"
```

If the check fails, stop and show the error. Do not run the pipeline on an
empty input file.

If the check passes, run:
```bash
python3 scripts/process_job_input.py
```

Present the full output. Highlight:
- The job's score and priority (A/B/C/Reject)
- Any hard reject reasons
- Whether it was accepted into the tracker

After presenting results, if the job was accepted with priority A or B,
say:
"This job is in the tracker as READY_FOR_APPROVAL. To apply, you must review
it and give explicit approval — the system will not submit anything automatically."

---

### `/jobos search`

Run:
```bash
python3 scripts/query_planner.py
```

Then run:
```bash
python3 scripts/source_registry.py
```

Present the output. Then explain the current state honestly:

"The query planner has generated [N] planned queries across [sources],
[roles], and [locations].

Four sources are DIRECT ENABLED, live-validated (NAUKRI, HIRIST, IIMJOBS,
APNA — scripts/naukri_adapter.py, hirist_adapter.py, iimjobs_adapter.py,
apna_adapter.py). Seven more restricted boards (LinkedIn, Indeed, Foundit,
Instahyre, Cutshort, Wellfound, Shine) are AVAILABLE_VIA_SEARCH_PROVIDER —
discovered through the configured multi-provider search layer
(scripts/search_provider_manager.py: You/Tavily/Exa/Brave/Serper, with
automatic failover on real provider failures only) rather than direct
crawling, since no authorized direct-crawl path exists for them; each is
ENABLED only when a provider API key is actually configured, otherwise
NOT_ENABLED with zero network calls. A separate set of Phase 4 skeletons
(LINKEDIN/INDEED/etc. as plain registry keys, plus TIMESJOBS,
CAREER_PAGE, GREENHOUSE, LEVER, ASHBY, WEB_SEARCH) remain NOT_ENABLED,
reserved for a hypothetical future authorized direct crawler. Glassdoor
is not part of the agreed source list and is not registered.

Turning a NOT_ENABLED direct-crawl skeleton into a real adapter follows
the same phased process Naukri/Hirist/IIMJOBS/APNA went through: offline
implementation + tests, one live query, controlled multi-query
validation, only then marked ENABLED.

To add a real job you found manually, use `/jobos add`."

Do not simulate or invent job search results. Do not browse job boards.

---

### `/jobos tests`

Run the full test suite:
```bash
cd /Users/sarojnayak/SarojJobOS && python3 scripts/test_scoring.py && echo "---" && python3 scripts/test_adapter_pipeline.py && echo "---" && python3 scripts/test_query_orchestrator.py
```

Present the output. Report PASS or FAIL clearly.

Note: `test_tracker.py` and `test_registry_pipeline.py` write TEST/MOCK
records to the live DB. Run them only when explicitly requested.

---

## Absolute Rules (non-negotiable)

These rules come from `CLAUDE.md` and override everything else.

1. **Never submit an application without explicit human approval.** No
   exception. If a user asks you to "apply to this job", respond: "I
   cannot submit applications automatically. Once a job is APPROVED in the
   tracker, you control the application step."

2. **Never fabricate experience, skills, employers, projects, certifications,
   responsibilities, leadership, or metrics.** If a job requires something
   not in `config/profile.json`, flag the gap — do not invent it.

3. **Never bypass CAPTCHA, MFA, login walls, rate limits, or anti-bot
   protections.** If a website blocks access, stop and report it.

4. **Do not create a second scoring system.** The 100-point engine in
   `scripts/score_job.py` is the only scoring system. Do not estimate,
   guess, or re-score jobs in natural language.

5. **Do not maintain a separate tracking list in the session.** SQLite
   `data/applications/jobos.db` is the only system of record.

6. **Do not generate or present jobs that are not in the database.** All
   job information must come from the pipeline output or the DB.

---

## Profile Reference

The candidate profile is at `config/profile.json`. Do not ask the user to
re-enter their profile. If you need profile details for a response (e.g.,
to explain why a job was rejected), read the config file:

```bash
python3 -c "import json; p=json.load(open('config/profile.json')); print(json.dumps(p['candidate'], indent=2))"
```

---

## Cover Letter and Resume Tailoring (future capability)

When resume tailoring or cover letter generation is requested for a
specific job:

1. Read `config/profile.json` — this is the only source of truth for
   skills, experience, certifications, and achievements.
2. Read the job's `jd_text` from the DB for the specific job_id.
3. Generate materials that mirror JD keywords but never add technologies,
   years, employers, or certifications not present in the profile.
4. Flag any JD requirement that has no corresponding evidence in the
   profile rather than inventing it.
5. Produce human-readable, non-clichéd language — avoid "spearheaded",
   "leveraged", "results-driven", "dynamic".
6. Keep cover letters under 300 words.

This capability is not yet implemented in the Python pipeline. Until it is,
you may assist with it manually in-session, strictly following rules 1–4
above.
