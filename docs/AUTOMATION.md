# Automation

**n8n is entirely optional. JobOS works fully without it** — every
automation below has a manual equivalent you can click yourself.

| Automation | Trigger | Frequency | Required Config | Input | Output | Writes Data? | Auto-submits applications? | How to disable | Troubleshooting |
|---|---|---|---|---|---|---|---|---|---|
| **Scheduled Search Runner** | n8n Schedule Trigger (or `scripts/run_scheduled_searches.py --once` via cron/launchd) | Every 30 min (n8n default) | `jobos_base_url` pointed at your production/automation JobOS instance | None — reads due schedules from JobOS itself | Triggers any search whose own saved schedule says it's due | **Yes** — a normal search run, scored and saved like any manual run | **No** | Deactivate the n8n workflow, or stop running the cron/launchd job. Each individual search's own schedule (set on the search's detail page) is separate — disable it there if you only want to stop *that* search. | [N8N_SETUP.md](N8N_SETUP.md) §17; check `POST /api/scheduler/run-due` responds 200 directly via curl. |
| **Follow-Up Reminder** | n8n Schedule Trigger, daily (8:00 AM default) | Once/day | `jobos_base_url`, `candidate_id` | `GET .../follow-ups?due_only=true` | Logs due/overdue follow-ups to n8n's own Executions tab | **No** — read-only | **No** | Deactivate the n8n workflow. | [N8N_SETUP.md](N8N_SETUP.md) §16-17 |

## What's NOT automated

- **Resume tailoring** is deterministic but **on-demand only** — you
  click "Tailor Resume" for a specific job; nothing runs it for you on
  a schedule.
- **Notifications** — not implemented. The Follow-Up Reminder workflow
  only logs to n8n's own execution log by default; wiring in an actual
  email/Slack/Telegram node is something you'd add yourself.
- **Application submission** — never automated, by design. See
  [APPLICATION_TRACKER.md](APPLICATION_TRACKER.md).

## Candidate ID in automation

Never hardcode someone else's candidate ID. Use
`candidate_id=<your-candidate-id>` — get your own via `GET
/api/candidates`. See [N8N_SETUP.md](N8N_SETUP.md) §8.

## macOS-only alternative (advanced, optional)

A `launchd` template (`launchd/com.sarojjobos.dailysearch.plist`)
exists for macOS users who prefer a native OS scheduler instead of
n8n/cron. It is **not installed by default**, is **macOS-specific**,
and is explicitly an advanced/optional alternative — normal users
should prefer n8n (cross-platform) or `cron`/Task Scheduler for the
same job. See `launchd/README.md` for its own setup steps if you want
it.
