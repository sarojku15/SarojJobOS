# Installation & Production Startup Guide

This guide takes you from a fresh clone to a verified **production-mode**
JobOS instance on **port 8421**, using the real database
(`data/applications/jobos.db`) — not the development server on port
8420. For a faster path to just trying JobOS out (dev mode, port 8420),
see [GETTING_STARTED.md](GETTING_STARTED.md) instead; this guide exists
specifically for the production/automation entry point that
[CONFIGURATION.md](CONFIGURATION.md)'s "Two API instances" section
describes, with more detail on first-run safety, verification, and
troubleshooting.

**Development vs. production, at a glance:**

| | Development | Production |
|---|---|---|
| Port | `8420` | `8421` |
| Database | `data/applications/jobos_dev.db` | `data/applications/jobos.db` |
| Start command | `uvicorn api.main:app --reload --port 8420` | `scripts/run_production_api.py` |
| Auto-reload | Yes (`--reload`) | No |
| Schema upgrades | Automatic, every request | Explicit, one command, you run it |
| Who uses it | You, trying things out / developing | n8n, cron, or anyone who wants their real, durable candidate data |

These two are genuinely separate SQLite files and separate processes —
running both at once is normal and safe; they never interfere with
each other.

---

## A. Prerequisites

Verified against this repository's actual dependency files
(`requirements.txt`, `package.json`) and [GETTING_STARTED.md](GETTING_STARTED.md):

- **Python 3.11+** (this project is developed/tested on 3.13).
- **Node.js 18+** — needed only for Playwright, which powers the direct
  Naukri/Hirist/IIMJobs/Apna browser-automation adapters.
- **Git**.
- **Operating system**: macOS or Linux have been the actually-tested
  platforms for this project's own development; Windows is documented
  in [GETTING_STARTED.md](GETTING_STARTED.md) from the project's real
  requirements, but has not been independently executed on a Windows
  machine in this project's own testing — this guide notes that
  distinction rather than claiming otherwise.
- No other system packages are required (no database server, no
  message broker — SQLite is a single file, created automatically).
- Disk space: no specific figure is substantiated by this repository;
  the production database is a single SQLite file that grows with your
  own job/search history (a few MB per few hundred jobs, from
  observation of this project's own data, not a documented guarantee).

## B. Clone the repository

```bash
git clone https://github.com/sarojku15/SarojJobOS.git
cd SarojJobOS
```

(If you already have a local clone, `cd` into it instead — do not
re-clone over an existing installation; see Section G for how an
existing installation differs from a fresh one.)

## C. Python environment setup

**macOS / Linux:**
```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

**Windows (PowerShell)** — documented from the project's stated
requirements, not independently verified on Windows in this project's
own testing:
```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\pip install -r requirements.txt
```

The exact dependencies installed (from `requirements.txt`): `fastapi`,
`uvicorn`, `starlette`, `pydantic`/`pydantic_core`, `httpx`,
`python-multipart`, `pypdf` (resume parsing), `openpyxl` (Excel
export).

If PowerShell blocks the venv's own activation script with an
execution-policy error, you do not need to activate it — every command
in this guide calls the venv's own Python/uvicorn binary directly
(`.venv/bin/...` or `.venv\Scripts\...`), which works without
activation.

## D. Frontend setup

JobOS's frontend is plain static HTML/CSS/JavaScript served directly by
the FastAPI app from the `web/` directory (`app.mount("/static", ...)`
in `api/main.py`) — there is **no frontend build step, no bundler, and
no separate frontend server or process**. `npm install` is needed for
exactly one thing: installing the `playwright` package
(`package.json`'s only real dependency), used by the Naukri/Hirist/
IIMJobs/Apna adapters' browser automation — not for the UI itself.

```bash
npm install
```

## E. Browser automation setup

```bash
npx playwright install chromium
```

This downloads the Chromium browser binary Playwright needs (cached
outside the repo, per-machine — e.g. `~/Library/Caches/ms-playwright`
on macOS). Run it once per machine. No other browser is required; only
Chromium is installed/used.

- Automated/background runs (the search worker, and this install
  process in general) always run headless by default — see
  `JOBOS_BROWSER_HEADLESS` in Section F if you ever need to debug with
  a visible window.
- No additional OS packages are documented as required by this
  repository beyond what `npx playwright install chromium` itself
  installs; if the install step reports missing system libraries on
  Linux, follow Playwright's own on-screen instructions for your
  distribution (outside this repository's scope to enumerate).

## F. Environment configuration

```bash
cp config/jobos.env.example .env
```

**Everything in `.env` is optional** — JobOS runs fully, with 4 direct
job-board sources (Naukri, Hirist, IIMJobs, Apna), with zero variables
set. `.env` is gitignored (`.gitignore`'s `.env` / `.env.*` entries) —
**never commit it**, and this guide will not show you real values.

| Variable | Required? | Purpose |
|---|---|---|
| `YOU_API_KEY`, `TAVILY_API_KEY`, `EXA_API_KEY`, `BRAVE_API_KEY`, `SERPER_API_KEY` | Optional | Each is a **BYOK** (Bring Your Own Key) credential for one search-provider API. Configuring **any one** unlocks 7 additional, otherwise-uncrawlable job boards (LinkedIn, Indeed, Foundit, Instahyre, Cutshort, Wellfound, Shine) via that provider's own search API. |
| `SEARCH_PROVIDER_ENABLED`, `SEARCH_PROVIDER_ORDER`, `SEARCH_PROVIDER_MAX_DAILY_REQUESTS`, `SEARCH_PROVIDER_MAX_MONTHLY_REQUESTS`, `SERPER_GL`, `SERPER_TIMEOUT` | Optional | Tuning for the search-provider layer; all have working defaults. |
| `JOBOS_BROWSER_HEADLESS` | Optional | Unset (default) = headless. Set to `0` only for interactive debugging — automated/background runs must stay headless. |
| `JOBOS_NAUKRI_USER_AGENT` | Optional | Overrides the User-Agent presented to Naukri. Unset falls back to a live-validated default. |
| `JOBOS_DB_PATH` | **Not for `.env`** | This is set only by `scripts/run_production_api.py` itself, in its own process environment, before it imports the app — see Section H. Setting it in `.env` has no special effect; `.env` loading and `JOBOS_DB_PATH` resolution are two independent mechanisms. |

**BYOK — the important part**: JobOS ships with **no key of any
kind** and never uses anyone else's. Each of the five provider keys
above is one **you** create on your own free/trial account with that
provider (You.com, Tavily, Exa, Brave, or Serper) — full instructions
in [API_PROVIDER_SETUP.md](API_PROVIDER_SETUP.md). You can also add a
key later, after the app is running, from **Settings → Search
Providers** in the UI (never through `.env` editing at that point) —
either path is supported; they write to the same place.

## G. Production database setup and safety

**Database path**: `data/applications/jobos.db`, resolved by
`api/db.py` — it is used **only** when the `JOBOS_DB_PATH` environment
variable is set to it, which happens in exactly one place in this
repository: `scripts/run_production_api.py`, at its own process
startup, before `api.main` is ever imported. No client request,
header, or query parameter can redirect an already-running process to
a different database.

**What happens on a first installation (no `jobos.db` yet):**
`scripts/run_production_api.py` checks for the file's existence *before
starting* and refuses to start if it's missing (`Production DB not
found at ... -- refusing to start.`) — it does **not** create one for
you, unlike the dev database. You must run the production migration
once first (below), which creates the file as part of building the
schema from scratch.

**What happens if `jobos.db` already exists (an existing installation):**
Nothing is touched automatically. The production API process
(`run_production_api.py`) only ever *serves* whatever schema already
exists on disk — it never runs a migration itself (confirmed in its own
docstring and source). If your existing `jobos.db` predates a schema
change this version of the code expects (for example, the `scoring_profiles`
table), API calls that need the new table/column will fail with a
`500` and a `no such table`/`no such column` error — see
Section J for the fix (the same `--confirm` command below, which is
safe to re-run and is a no-op if already current).

**Do migrations run automatically? No — this is explicit, by design.**
Run:

```bash
.venv/bin/python3 scripts/migrate_production_schema.py --confirm
```

`--confirm` is a required flag — the script refuses to run without it,
so it can never be triggered by accident. Before touching anything, it:

1. Refuses to proceed if `PRAGMA integrity_check` on the existing file
   fails.
2. Copies a timestamped backup to `data/backups/` (gitignored) and
   verifies *that backup's own* integrity before continuing.
3. Records every pre-existing table's row count.
4. Runs the full migration chain (every step is `CREATE TABLE IF NOT
   EXISTS` / `ADD COLUMN IF NOT EXISTS`-equivalent — never destructive,
   never alters an existing column's data).
5. Re-checks integrity and confirms **no table's row count decreased**
   and your candidate record is unchanged, aborting loudly (with the
   exact `cp <backup> <db>` restore command) if anything looks wrong.

This is the **only** supported way to change production's schema. It
is safe to run again later — it is idempotent and reports success with
zero new tables if your schema is already current.

**On a genuinely fresh installation** (no `jobos.db` exists yet), the
same command creates it from scratch with the full current schema —
there is no separate "initialize" step; migration and initialization
are the same command for a file that doesn't exist yet.

**Never do this:**
- Never copy `jobos_dev.db` over `jobos.db`, or vice versa — they are
  deliberately separate, independent files; copying one over the other
  discards whichever one you overwrote.
- Never delete or hand-edit `jobos.db` directly.
- Never run `scripts/init_dev_db.py` against the production path — it
  contains an explicit guard that refuses to run against a path that
  resolves to `data/applications/jobos.db`.

**Backing up before any migration** is already built into
`migrate_production_schema.py --confirm` itself (step 2 above) — you
do not need a separate manual backup step, though you're always free
to additionally copy `data/applications/jobos.db` somewhere yourself
first if you want extra peace of mind:

```bash
cp data/applications/jobos.db data/applications/jobos.db.manual-backup-$(date +%Y%m%d)
```

## H. Start in production mode on port 8421

```bash
.venv/bin/python3 scripts/run_production_api.py
```

This is the **only correct production startup command** — do not take
the development command (`uvicorn api.main:app --reload --port 8420`)
and simply change the port number. That bare-uvicorn pattern never
sets `JOBOS_DB_PATH`, so it would silently open `jobos_dev.db` while
listening on port 8421 — a real incident this exact mistake caused
once in this project, which is why `api/main.py` now has an explicit
middleware guard that **refuses every request on port 8421 with an
HTTP 500** if the serving process isn't genuinely in production mode
(see Section I's verification step and
[CONFIGURATION.md](CONFIGURATION.md)).

What this command does, read directly from its own source
(`scripts/run_production_api.py`):
- Refuses to start if `data/applications/jobos.db` doesn't exist yet
  (see Section G — run the migration first).
- Sets `JOBOS_DB_PATH` to the real production path, in this process's
  own environment only, before importing the app.
- Binds to **`127.0.0.1` only** (not `0.0.0.0`) — this is explicitly a
  local automation endpoint, never meant to be exposed on a network
  interface. See Section L.
- Runs **without** `--reload` by default (a `--reload` flag exists on
  the script but is off unless you explicitly pass it — never use it
  for a real production/automation process).
- Default port is **8421**; override with `--port` if you ever need
  to, though every other doc/script in this project assumes 8421.

```bash
.venv/bin/python3 scripts/run_production_api.py --port 8421   # equivalent -- 8421 is already the default
```

Open the UI at **http://127.0.0.1:8421/**. From inside an n8n Docker
container specifically, reach it at `http://host.docker.internal:8421`
instead — `localhost`/`127.0.0.1` inside that container resolves to the
container itself, not your Mac/host.

## I. Verify the installation

**1. Confirm the process is actually listening on 8421:**

```bash
lsof -i :8421          # macOS/Linux
```
Expect one `python` (or `Python`) process shown as `LISTEN` on that
port.

**2. Confirm it reports production mode and the right database** (the
single most important check — this is the exact field that catches the
dev/production mix-up this guide exists to prevent):

```bash
curl -s http://127.0.0.1:8421/api/health
```
Expected: `{"status":"OK","db":"jobos.db","mode":"production","jobos_db_path_configured":true}`.
If you instead see `"db":"jobos_dev.db"` or `"mode":"development"`,
you started the wrong command — stop it and use Section H's command
exactly.

**3. Confirm the UI loads:** open `http://127.0.0.1:8421/` in a
browser — you should see the JobOS home page (onboarding screen for a
genuinely new installation, or your existing dashboard for an existing
one).

**4. Confirm a real API request succeeds**, e.g. list sources:
```bash
curl -s http://127.0.0.1:8421/api/sources
```
Expect a JSON body listing the enabled sources (Naukri, Hirist,
IIMJobs, Apna at minimum), not an error.

**5. Confirm it isn't accidentally the dev DB** — beyond the `/api/health`
check above, you can independently cross-check row counts never move
on the dev file while you use the production UI:
```bash
ls -la data/applications/jobos_dev.db data/applications/jobos.db
```
Only `jobos.db`'s modification time should change as you use the
production instance at port 8421.

## J. Troubleshooting

**Port 8421 already in use.**
```bash
lsof -i :8421
```
If it's a previous `run_production_api.py` you forgot about, stop that
process (`kill <pid>`) before starting a new one — do not run two
production processes against the same SQLite file simultaneously.

**`ModuleNotFoundError` / missing Python packages.** Confirm you're
using the venv's own Python, not a system one:
```bash
.venv/bin/python3 -c "import fastapi, uvicorn; print('ok')"
```
If this fails, re-run `.venv/bin/pip install -r requirements.txt`.

**Missing Playwright browser binary** (`Executable doesn't exist...`
error from a search involving Naukri/Hirist/IIMJobs/Apna):
```bash
npx playwright install chromium
```

**"Refusing to serve requests on port 8421" (HTTP 500).** You started
a bare `uvicorn api.main:app --port 8421` instead of
`scripts/run_production_api.py` — this is the production-port guard
(`api/main.py`) doing exactly what it's supposed to. Stop that process
and use Section H's command.

**Wrong working directory / import errors.** Every command in this
guide assumes your shell's current directory is the repository root
(`SarojJobOS/`) — `cd` there first; `scripts/run_production_api.py`
adds the repo root to `sys.path` itself, but only if invoked from (or
able to resolve) the correct location.

**`.env` not being picked up.** `.env` must be at the repository root
(next to `requirements.txt`), not inside `config/`. Search-provider
keys are loaded automatically at import time — restart
`run_production_api.py` after editing `.env` for the new value to take
effect (environment variables are read once at process start, not
live-reloaded).

**Database path or permission errors.** Confirm the file exists and is
readable/writable by your user:
```bash
ls -la data/applications/jobos.db
```
If it's missing, see Section G (run the migration to create it). If
it exists but errors on open, check file permissions — never `chmod
777` a database containing your own data; fix ownership instead
(`chown` to your own user).

**Frontend dependencies missing / blank page.** The frontend has no
separate dependencies to be "missing" (see Section D) — a blank or
broken page is almost always a backend connectivity issue, not a
frontend build problem. Check the browser console and `/api/health`
first.

**Health-check failures** (`/api/health` unreachable or returns a
connection error). Confirm the process is actually running (`lsof -i
:8421`) and that you're hitting `127.0.0.1`, not `0.0.0.0` or an
external interface (the process binds to `127.0.0.1` only — see
Section L).

**Still stuck?** Run the full diagnostic:
```bash
.venv/bin/python3 scripts/jobos_doctor.py
```
(or `scripts/jobos-doctor` on macOS/Linux, a thin wrapper around the
same script) — it checks Python, Node, dependencies, database,
Playwright, and provider configuration, and reports exactly what's
missing. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for more.

## K. Stop, restart, and update

**Stop**: `Ctrl+C` in the terminal running `run_production_api.py` (it
runs in the foreground; there is no separate daemon/service-manager
layer in this repository).

**Restart**: re-run Section H's command. No state is lost — all data
lives in `jobos.db` on disk, independent of the process.

**Update from Git safely:**
```bash
git status                     # confirm no uncommitted local changes first
git pull
.venv/bin/pip install -r requirements.txt   # in case dependencies changed
npx playwright install chromium             # in case the pinned version changed
```
Then check whether the update added any new `migrate_vN_*.py` file to
`scripts/migrate_production_schema.py`'s own import list (look at that
file's diff, or just re-run the migration command below — it's always
safe) before restarting the production process:
```bash
.venv/bin/python3 scripts/migrate_production_schema.py --confirm
.venv/bin/python3 scripts/run_production_api.py
```

**Back up before updating**: the migration command itself takes a
timestamped backup automatically (Section G) every time you run it.
For extra safety before a `git pull` that includes a schema change,
you can additionally copy the database yourself first, as shown in
Section G.

## L. Security and production limitations

**State this plainly: JobOS, as shipped, is designed for local,
single-user use on your own machine — it is not internet-ready, and
this guide does not make it so.**

- `run_production_api.py` binds to `127.0.0.1` only. Nothing in this
  repository configures `0.0.0.0`, a public bind address, TLS/HTTPS, a
  reverse proxy, or a firewall rule.
- **No authentication token is required to call the API.** A
  `candidate_id` (stored in the browser's `localStorage`) scopes which
  candidate's data a request can see/modify, but it is **not** proof
  of identity — anyone who can reach the API at all can claim any
  `candidate_id`. What *is* enforced is that a search/run genuinely
  belongs to whichever `candidate_id` was supplied, so one candidate
  cannot read or guess into another's data *by ID* — but there is no
  login, no password, and no session concept at all.
- **Do not port-forward, reverse-proxy, or otherwise expose port 8421
  (or 8420) to your LAN or the public internet** without first adding
  a real authentication layer, HTTPS, and appropriate firewall rules —
  none of which this repository provides out of the box. Doing so
  without those controls would let anyone who can reach the port read
  or modify whatever candidate data exists.
- If multiple people share one installation, they also share one set
  of search-provider API keys (BYOK keys are installation-scoped, not
  per-candidate) — see [SECURITY.md](SECURITY.md)'s "API keys" section
  for the full detail and the recommended workaround (separate
  installations per person).
- This guide and this repository do not claim multi-tenant,
  internet-facing security — if you need that, you would need to add
  your own authentication/authorization layer in front of this API
  before exposing it beyond `127.0.0.1`.

---

## See also

- [GETTING_STARTED.md](GETTING_STARTED.md) — the faster dev-mode
  (port 8420) walkthrough, including your first search.
- [CONFIGURATION.md](CONFIGURATION.md) — every environment variable
  and config file, and the full "Two API instances" explanation this
  guide expands on.
- [SECURITY.md](SECURITY.md) — the full privacy/security model.
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — the project-wide
  troubleshooting index.
