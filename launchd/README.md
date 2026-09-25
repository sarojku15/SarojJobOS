# SarojJobOS Daily Search — launchd Integration (NOT installed)

Phase 7.2 (`data/reports/phase7_2_automation_status_audit.md`). This
directory contains a **project-owned template**, not an active
scheduled job. Nothing in this repository installs, loads, or starts
it. Installing it is a deliberate, manual, human-approved step — do it
only when you're ready for the daily search to actually run
unattended.

## What gets installed

`com.sarojjobos.dailysearch.plist` tells macOS's `launchd` to run
`scripts/run_daily_search.sh` once a day (08:00 local time by default
— edit the `StartCalendarInterval` block first if you want a different
time). That wrapper script:

1. Computes today's date and creates `data/daily/YYYY-MM-DD/`.
2. Refuses to run if a previous invocation is still in progress (lock
   file under `logs/`).
3. Runs `.venv/bin/python3 scripts/run_search_worker.py --once
   --candidate-id saroj --db data/applications/jobos.db --report-out
   data/daily/YYYY-MM-DD/job_search_report.xlsx` — one bounded search
   cycle against the **real** production database, headless Chromium
   forced on.
4. Writes `data/daily/YYYY-MM-DD/worker.log` (detailed) and appends a
   one-line status to `logs/daily_search_scheduler.log` (summary).
5. Never submits an application. Never deletes a previous day's
   workbook. Never retries automatically.

## Installation (manual — run these yourself, when ready)

```bash
# 1. Copy the plist into your LaunchAgents directory
cp /Users/sarojnayak/SarojJobOS/launchd/com.sarojjobos.dailysearch.plist \
   ~/Library/LaunchAgents/com.sarojjobos.dailysearch.plist

# 2. Load it (registers the job with launchd; does NOT run it immediately --
#    RunAtLoad is false, so it only fires at the next StartCalendarInterval)
launchctl load ~/Library/LaunchAgents/com.sarojjobos.dailysearch.plist

# 3. (Optional) Trigger one run immediately, to confirm it works, without
#    waiting for the schedule:
launchctl start com.sarojjobos.dailysearch
```

## Checking on it later

```bash
# Is it loaded?
launchctl list | grep sarojjobos

# Scheduler-level log (launchd's own stdout/stderr capture)
tail -f ~/SarojJobOS/logs/launchd_dailysearch.out.log
tail -f ~/SarojJobOS/logs/launchd_dailysearch.err.log

# Today's detailed worker log and one-line scheduler status
tail -f ~/SarojJobOS/data/daily/$(date +%Y-%m-%d)/worker.log
tail -f ~/SarojJobOS/logs/daily_search_scheduler.log
```

## Uninstalling

```bash
launchctl unload ~/Library/LaunchAgents/com.sarojjobos.dailysearch.plist
rm ~/Library/LaunchAgents/com.sarojjobos.dailysearch.plist
```

## Before you install

- Edit the plist's `StartCalendarInterval` if 08:00 local doesn't suit
  you.
- Confirm `.venv/bin/python3` exists and has `openpyxl` installed
  (`requirements.txt`) — the wrapper script hardcodes this path.
- The candidate ID is hardcoded to `saroj` in both the wrapper script
  and this plist's intent — this project has exactly one real
  candidate today.
- Only `NAUKRI` is `ENABLED` — this daily run will only ever query
  Naukri. Hirist and LinkedIn remain `NOT_ENABLED` and are untouched by
  this automation.
- Test first with `scripts/run_daily_search.sh --dry-run` (prints what
  would happen, touches nothing, makes no network call) before
  installing the real scheduled job.
