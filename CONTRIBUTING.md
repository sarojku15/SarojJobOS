# Contributing to JobOS

Thanks for considering a contribution. JobOS is a local-first job
search tool — contributions should keep that model intact: no feature
should require a hosted service, a shared credential, or anyone's
personal data to work.

## Before you start

- Read [README.md](README.md) and [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) for what the app actually does.
- Read [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the real repository structure and where to make common changes.
- **Never develop against anyone's production environment.** Everything below uses your own local dev database and your own (optional, never required) provider keys.

## Workflow

### macOS / Linux

```bash
# 1. Fork on GitHub, then clone your fork
git clone https://github.com/<your-username>/SarojJobOS.git
cd SarojJobOS

# 2-3. Virtual environment + dependencies
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm install
npx playwright install chromium

# 6. Run JobOS (dev mode — jobos_dev.db, never your production data)
.venv/bin/uvicorn api.main:app --reload --port 8420

# 7. Run tests (in a separate terminal)
for f in scripts/test_*.py; do .venv/bin/python3 "$f"; done
.venv/bin/python3 scripts/test_frontend_integrity.py

# 8. Create a feature branch
git checkout -b my-feature

# ... make your change ...

# 12-13. Commit and push to your fork
git add <files>
git commit -m "Describe what changed and why"
git push -u origin my-feature
```

### Windows (PowerShell)

```powershell
git clone https://github.com/<your-username>/SarojJobOS.git
cd SarojJobOS

py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
npm install
npx playwright install chromium

.venv\Scripts\uvicorn api.main:app --reload --port 8420

# In a separate terminal:
Get-ChildItem scripts\test_*.py | ForEach-Object { .venv\Scripts\python $_.FullName }
.venv\Scripts\python scripts\test_frontend_integrity.py

git checkout -b my-feature
git add <files>
git commit -m "Describe what changed and why"
git push -u origin my-feature
```

**Windows has not been independently tested in this project's own
development** — the commands above follow the actual `requirements.txt`/
`package.json` and should work, but if you hit something OS-specific,
please say so in your PR rather than assume it's just you.

### 14. Open a Pull Request

Open the PR against `main` on the upstream repository. Fill in the
[PR template](.github/pull_request_template.md) — it will appear
automatically.

## Testing expectations

- **Add or update a test for any behavior change.** The existing suite
  (104 files under `scripts/test_*.py`, plus `scripts/test_frontend_integrity.py`)
  is the pattern to follow — most tests use an isolated temp SQLite DB
  via `tempfile.mkdtemp()`, never the real `data/applications/jobos.db`
  or `jobos_dev.db`.
- **Never point a test at a real database.** If your change touches
  something DB-related, redirect the relevant module's `DB_PATH`/
  `DATA_DIR` to a temp directory the way existing tests do (see
  `scripts/test_migrate_v2_schema.py` for the clearest example).
- Run the full suite before opening a PR:
  ```bash
  for f in scripts/test_*.py; do .venv/bin/python3 "$f"; done
  .venv/bin/python3 scripts/test_frontend_integrity.py
  ```

## Never commit

- `.env` (your own provider keys)
- Any `.db`/`.sqlite` file
- Anything under `resumes/` or `resumes_dev/`
- A real candidate profile or resume content
- Real provider API keys, tokens, or passwords
- Machine-specific absolute paths in new code/docs
- Personal reports/logs containing identifying information

Use **synthetic data** instead: a fictional candidate ("Alex Doe"-style,
see [docs/FIRST_SEARCH_WALKTHROUGH.md](docs/FIRST_SEARCH_WALKTHROUGH.md)),
the synthetic resume fixture at `data/fixtures/resume_extractor/synthetic_sre_resume.pdf`
(see `scripts/generate_synthetic_resume_pdf.py` if you need to produce
a similar one), fake provider keys like `"fake-key-for-this-test"`
(the existing convention in `scripts/test_phase14_search_provider.py`
and others), and a temp/isolated database.

## Scope of changes

- Matching/scoring logic ([docs/MATCHING.md](docs/MATCHING.md),
  [docs/SCORING.md](docs/SCORING.md)) and the Application Tracker
  lifecycle ([docs/APPLICATION_LIFECYCLE.md](docs/APPLICATION_LIFECYCLE.md))
  are deliberately stable, tested contracts — changing their *behavior*
  (not just refactoring) should be discussed in an issue first.
- New job sources/providers: see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)'s
  "Adding a new source" section.
- Never add a feature that submits an application automatically, bypasses
  CAPTCHA/MFA, or requires a specific person's credentials to function.

## Code of Conduct

By participating, you agree to abide by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Security issues

Do not open a public issue for a security vulnerability — see
[SECURITY.md](SECURITY.md).
