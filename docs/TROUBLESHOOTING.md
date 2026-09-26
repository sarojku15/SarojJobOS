# Troubleshooting

Real, discoverable problems only — nothing invented.

## Installation

**`npx playwright install chromium` fails, or Naukri/Hirist/IIMJobs/Apna
searches error out.** Playwright's browser binary is a per-machine
cache (`~/Library/Caches/ms-playwright` on macOS), not part of this
repo — re-run the install command.

**`pip install -r requirements.txt` fails.** Check your Python version
(`python3 --version`) — this project is developed/tested on 3.13,
requires 3.11+. Use a fresh virtualenv (`python3 -m venv .venv`) rather
than a system Python.

**`npm install` fails or is slow.** Only `playwright` (`^1.63.0`) is a
real dependency — check `package.json`. Node 18+ is required.

## Startup

**Port 8420 already in use.** Another instance is likely still running
(`ps aux | grep uvicorn`) — stop it, or start this one on a different
port (`--port 8421`) and open that URL instead.

**Nothing in `data/applications/` after first run.** The dev database
(`jobos_dev.db`) is created automatically on the first API request, not
at process startup — open the app in a browser first.

## Using the app

**"Profile needs to be confirmed" when trying to run a search.** Go to
`/profile`, review the extracted/entered fields, and click "Confirm
profile" — a `DRAFT` profile cannot be used to run a search.

**A search-provider board (LinkedIn/Indeed/etc.) never appears as an
option.** No provider API key is configured in `.env` yet — check
`GET /api/sources` or Settings → Search Providers for the live reason
(`NOT_CONFIGURED` vs an actual failure). See
[JOB_SOURCES.md](JOB_SOURCES.md).

**A search finishes but a source shows `FAILED` or `BLOCKED`.** This is
truthful reporting, not a bug to "fix" by retrying harder — the source
either errored or actively blocked the request (e.g. a CAPTCHA
challenge). JobOS will not attempt to bypass it. Try again later, or
proceed with the sources that did succeed.

**Export/Excel job count looks different from the results page.** It
shouldn't — both read from the same canonical, run-scoped functions
(`api/results_store.py`'s `_job_ids_for_search()`). If you genuinely see
a mismatch, that's a regression worth reporting with the specific
`search_id`/`run_id`.

## Database

**Dev database schema looks out of date / a column is missing.** Delete
`data/applications/jobos_dev.db` and restart the app — it's regenerated
automatically via `scripts/init_dev_db.py`'s migration chain. Never do
this to the production DB (`jobos.db`).

## Claude Code / Skills

**Claude doesn't seem to know about a JobOS skill.** Skills are
discovered from `.claude/skills/*/SKILL.md` in this repository — make
sure you're running Claude Code with this repo as (or inside) the
working directory. See [CLAUDE_GUIDE.md](CLAUDE_GUIDE.md) for the full
discovery mechanism.

**`/jobos` command behaves unexpectedly.** That command drives the
*legacy*, single-candidate CLI pipeline (`config/profile.json`,
`data/inbox/job_input.txt`) — a separate, older code path from the
multi-candidate web app/API most users interact with. See
[ARCHITECTURE.md](ARCHITECTURE.md) for how the two relate.

## Still stuck?

Run `scripts/jobos-doctor` for a real, automated check of your Python/
Node/dependency/database/Playwright/Claude-Skills setup — see
[GETTING_STARTED.md](GETTING_STARTED.md).
