# Security & Privacy

## Secrets

- `.env` (real API keys) is **gitignored** (`.gitignore`: `.env`,
  `.env.*`, `credentials.json`, `token.json`, `client_secret*.json`,
  `*.pem`, `*.key`) — never commit it. Only `config/jobos.env.example`
  (a template with no real values) is committed.
- No API key is ever logged, returned in an API response, or written
  into a report/export.

## Candidate & resume privacy

- `resumes/` (uploaded resume files) and `data/applications/` (the
  production database and application data) are gitignored — never
  committed.
- Each candidate's resumes, profile, searches, and results are strictly
  scoped to their own `candidate_id`. Verified by
  `scripts/test_multi_user_full_isolation.py` and
  `scripts/test_candidate_ownership_isolation.py`: candidate A cannot
  read, list, or guess their way into candidate B's searches, resumes,
  results, dashboard, or exports.
- **Important limitation, stated plainly**: `candidate_id` is stored in
  the browser's `localStorage` (`web/app.js`), not behind a real login.
  Any caller can *claim* to be any `candidate_id` — there is no proof of
  identity. What **is** enforced server-side is that a search/run
  actually *belongs to* whichever `candidate_id` was supplied
  (`search_store.get_saved_search_for_candidate()` /
  `get_run_status_for_candidate()`), so guessing/reusing another
  candidate's search/run ID doesn't work. A real internet-facing
  deployment would still need actual authentication layered on top of
  this.

## Job-board credentials

JobOS does not store or require your own job-board login credentials
for any source. The 4 direct sources are crawled anonymously/publicly;
the 7 provider-backed sources go through third-party search APIs, not
your own job-board account.

## Browser automation boundaries (non-negotiable)

- Never bypasses CAPTCHA, MFA/OTP, or anti-bot protections.
- Never creates fake accounts.
- Respects `robots.txt` (e.g. Hirist's `Crawl-delay: 10`).
- If a site blocks automation, the source is reported `BLOCKED` — JobOS
  stops, it does not try to work around it.
- No automated application submission exists anywhere in the codebase
  (`api/`, `web/`) — see [APPLICATION_LIFECYCLE.md](APPLICATION_LIFECYCLE.md).

## Logs & data at rest

- `logs/` and `*.log` are gitignored.
- `n8n/*` (runtime database + encrypted credentials for the n8n
  container) is gitignored in full.
- `browser/sessions/`, `browser/.auth/`, `playwright/.auth/` are
  gitignored — no browser session/auth state is ever committed.
- The production SQLite DB is plain, unencrypted, local-only — treat
  the machine it's on as the trust boundary, same as any local desktop
  app storing personal data.

## Database access

There is exactly one production DB file
(`data/applications/jobos.db`) and one dev DB
(`data/applications/jobos_dev.db`, safe to delete/regenerate). Neither
is ever touched by automated tests — every test suite in this project
either uses a fresh isolated temp DB or, where it must open a real DB
path, verifies the production DB's SHA-256/size is byte-identical
before and after.

## API access

No authentication token is required to call the local API — it's
designed to run on your own machine (`127.0.0.1`), not to be exposed to
the internet. Do not port-forward or reverse-proxy this app to the
public internet without adding real authentication first.

## Reporting a concern

If you find a way to bypass the ownership checks above, or any other
security issue, do not exploit it — open an issue describing the class
of problem (not a working exploit).
