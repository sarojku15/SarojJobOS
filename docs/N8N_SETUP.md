# n8n Setup

## 1. What n8n is

[n8n](https://n8n.io) is a workflow-automation tool. In this project it
plays exactly one role: an external clock that periodically calls two
of JobOS's own read/write-safe API endpoints. It never talks to
JobOS's database directly.

## 2. Is n8n optional?

**Yes, entirely.** JobOS's own scheduler (`search_schedules` table,
`scripts/scheduler.py`) persists "this search runs hourly/daily/weekly"
state regardless of whether n8n exists. n8n (or the `cron`/`launchd`
alternative below) is only the thing that actually calls the trigger on
a timer.

## 3. What works without n8n

Everything except unattended, scheduled automation: creating a
candidate, uploading a resume, confirming a profile, configuring
providers, creating/running searches manually (clicking "Run now"),
reviewing results, tracking applications, scheduling follow-ups (the
follow-up *date* is yours to set regardless — only the daily *reminder
log* needs an external trigger), interview prep, company research, and
Excel export all work with n8n never installed.

## 4. Docker installation

This repository's `docker-compose.yml` already defines the n8n
container:

```bash
docker compose up -d
```

This starts n8n on port 5678, with `GENERIC_TIMEZONE`/`TZ` set to
`Asia/Kolkata` by default in the committed file — edit
`docker-compose.yml` yourself if you want a different timezone before
starting it the first time.

## 5. Opening n8n

```
http://localhost:5678
```

First launch asks you to create an n8n account (local to your n8n
instance — nothing to do with JobOS candidates).

## 6. Importing the workflows

Two workflow files are committed, importable as-is:

- `n8n/workflows/jobos_scheduled_search_runner.json` — **JobOS
  Scheduled Search Runner**
- `n8n/workflows/jobos_follow_up_reminder.json` — **JobOS Follow-Up
  Reminder**

In the n8n UI: **Workflows → Import from File**, pick each JSON file.

## 7. Required configuration per workflow

Each workflow has a **"JobOS Config"** node (a Set node) with two
values you must fill in before activating:

| Field | What to set it to |
|---|---|
| `jobos_base_url` | `http://host.docker.internal:<JOBOS_PORT>` — **never** `http://localhost:...` from inside the n8n container, since `localhost` inside a Docker container means the container itself, not your Mac/PC host. For real scheduled automation point this at your **production/automation** JobOS instance (see step 9), not the interactive dev server. |
| `candidate_id` | `<your-candidate-id>` — **your own** candidate ID, found via `GET http://127.0.0.1:<JOBOS_PORT>/api/candidates` or shown in JobOS's own UI after you create your candidate. Left blank in the committed files on purpose — never a real candidate ID is committed to this repository. |

Both committed files default `jobos_base_url` to
`http://host.docker.internal:8421` — JobOS's documented
production/automation port (see step 9) — which you should only need
to change if you run JobOS on a different port.

## 8. Candidate ID

Never hardcode someone else's candidate ID into your own copy of these
workflows. Get your own:

```bash
curl -s http://127.0.0.1:8421/api/candidates
```

Copy your `candidate_id` value into the JobOS Config node's
`candidate_id` field.

## 9. Dev vs. production/automation API — which port to point at

JobOS can run as two separate processes:
- **Dev** (`.venv/bin/uvicorn api.main:app --reload --port 8420`) —
  opens `jobos_dev.db`, for interactive use while you're working on it.
- **Production/automation** (`.venv/bin/python3
  scripts/run_production_api.py`, port `8421` by default) — opens the
  real `data/applications/jobos.db`.

**Point n8n at the production/automation instance (8421), never the
dev one** — real recurring automation against your interactive dev
database would be meaningless. See
[CONFIGURATION.md](CONFIGURATION.md) for the full dev/production
explanation.

## 10. Scheduled Search Runner — what it does

Calls `POST /api/scheduler/run-due` on a timer (every 30 minutes by
default). This endpoint is JobOS's own — it alone decides which saved
searches are actually due (based on each search's own schedule) and
triggers them. The workflow itself contains zero scheduling logic; it
is only the external clock.

## 11. Follow-Up Reminder — what it does

Calls `GET /api/candidates/<your-candidate-id>/follow-ups?due_only=true`
once a day (8:00 AM by default) and logs each due/overdue follow-up to
n8n's own Executions tab. It is **read-only** — it never writes
anything back to JobOS, and it never sends an external notification by
itself. To get a real notification (email/Slack/etc.), add your own
notification node after the existing "Log Due Follow-Ups" node — none
is wired in by default, since no notification provider is guaranteed
configured.

## 12. Timezone

Both workflows' Schedule Trigger nodes use whatever `GENERIC_TIMEZONE`
is set in `docker-compose.yml` (`Asia/Kolkata` by default). Change that
value and restart the n8n container to use a different timezone for
both workflows' daily/periodic triggers.

## 13. Activating a workflow

After filling in the JobOS Config node, toggle the workflow to
**Active** (top-right switch in the n8n editor). `RunAtLoad` is false
on both — activating does not immediately run them; they wait for
their next scheduled trigger.

## 14. Testing without waiting for the schedule

Use n8n's own **"Execute Workflow"** button in the editor to run it
once immediately. This is the one case where manually triggering a
workflow is expected and safe — neither workflow writes to JobOS or
submits anything, so a manual test execution carries no risk beyond
making one real HTTP call to your own JobOS instance.

## 15. Viewing execution history

**Workflows → (select workflow) → Executions** tab shows every past
run, its status, and (click into one) the exact request/response at
each node — the most reliable way to see what actually happened,
rather than relying on memory of a past run.

## 16. The "404 / stale execution" trap

If you see a 404 in an execution and the workflow's **current**
configuration (JobOS Config node, re-opened fresh) looks correct, check
the **execution's own timestamp** before assuming something is broken
right now:
- An execution only reflects the workflow's configuration **at the
  moment it ran** — if you fixed `jobos_base_url`/`candidate_id`/the
  URL's query string *after* that execution happened, the error you're
  looking at is historical, not current.
- Confirm by checking whether a *newer* execution exists after your
  fix, or by using "Execute Workflow" to generate one fresh.

## 17. API-connectivity troubleshooting

```bash
# From your Mac/PC host:
curl -i http://127.0.0.1:8421/api/health

# From inside the n8n container (should give the same result):
docker exec <your-n8n-container-name> node -e "
fetch('http://host.docker.internal:8421/api/health')
  .then(async r => console.log(r.status, await r.text()))
"
```
If the host curl works but the container fetch doesn't, the issue is
networking (wrong `jobos_base_url`, or JobOS bound to `127.0.0.1` only —
`host.docker.internal` requires the host process to accept connections,
which the default `uvicorn`/production launcher already does). If
neither works, JobOS itself isn't running/healthy — start it first.

## 18. Restarting n8n

```bash
docker compose restart n8n
```
A restart re-registers every active workflow's schedule trigger from
its current saved configuration — needed if you changed a workflow's
config via the CLI/API rather than through the running editor UI
(the editor applies changes live; out-of-band edits need a restart to
take effect).

## 19. Shutting down

```bash
docker compose down
```
This stops the container; your workflows and their execution history
persist in the container's own data volume and are there again next
`docker compose up -d`.

## 20. Backing up your workflows

Your own edits inside the n8n UI are **not** automatically reflected
back into this repository's `n8n/workflows/*.json` files — n8n and git
are two separate places. To capture your current live workflow
configuration into a file you control:

```bash
docker exec <your-n8n-container-name> n8n export:workflow --all --output=/tmp/my_workflows.json
docker cp <your-n8n-container-name>:/tmp/my_workflows.json ./my_n8n_backup.json
```

## 21. What n8n NEVER does in this project

- Never opens or writes to `jobos.db`/`jobos_dev.db` directly — every
  interaction goes through JobOS's own HTTP API.
- Never submits a job application.
- Never auto-approves or auto-advances an application's status.
- Never sends a notification unless you add that node yourself.
- Never requires any credential/API key of its own for either
  committed workflow (both make plain, unauthenticated HTTP calls to
  your own local JobOS instance).
