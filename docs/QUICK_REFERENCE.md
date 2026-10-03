# Quick Reference

One-page cheat sheet. Full detail is linked from each line.

| Action | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Install | `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && npm install && npx playwright install chromium` | `py -3 -m venv .venv; .venv\Scripts\pip install -r requirements.txt; npm install; npx playwright install chromium` |
| Start (dev) | `.venv/bin/uvicorn api.main:app --reload --port 8420` | `.venv\Scripts\uvicorn api.main:app --reload --port 8420` |
| Start (production/automation) | `.venv/bin/python3 scripts/run_production_api.py` | `.venv\Scripts\python scripts\run_production_api.py` |
| Stop | `Ctrl+C` in the running terminal | `Ctrl+C` in the running terminal |
| Open UI | `http://127.0.0.1:8420/` | same |
| Check health | `curl http://127.0.0.1:8420/api/health` | `curl http://127.0.0.1:8420/api/health` (or open the URL in a browser) |
| Diagnose setup | `scripts/jobos-doctor` | `.venv\Scripts\python scripts\jobos_doctor.py` |
| Create candidate/profile | Upload a resume or choose "Enter profile manually" on first visit | same |
| Upload resume | Profile page → upload `.pdf` | same |
| Edit profile | Profile page → edit fields → Save | same |
| Confirm profile | Profile page → "Confirm profile" (required before any search) | same |
| Add provider key | Settings → Search Providers → paste your own key → Save | same |
| Test provider | Settings → Search Providers → Test | same |
| Enable provider | Settings → Search Providers → Enable | same |
| Create search | `/searches/new` | same |
| Run search | Search detail page → "Run now" | same |
| View results | `/searches/{id}/results` | same |
| Shortlist / Approve | Job workspace → status dropdown | same |
| Company research | Job workspace → "Research Company" | same |
| Tailor resume | Job workspace → pick resume → "Tailor Resume" (deterministic, not AI) | same |
| Interview prep | Job workspace → "Generate Interview Preparation" | same |
| Apply manually | Apply on the employer's own site — JobOS never does this for you | same |
| Mark applied | Job workspace / My Applications → "Mark as Applied" | same |
| Add follow-up | Application view → "Schedule follow-up" | same |
| Complete follow-up | Application view → "Follow Up Now" | same |
| View dashboard | `/dashboard` | same |
| Export to Excel | Search results page → download report | same |
| Start n8n | `docker compose up -d` | `docker compose up -d` |
| Check n8n | `http://localhost:5678` | same |
| Backup | Copy `data/applications/*.db` and `.env` — see [DATA_AND_BACKUP.md](DATA_AND_BACKUP.md) | same, PowerShell `Copy-Item` |
| Troubleshoot | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | same |

Full guided tour for a brand-new user:
[FIRST_SEARCH_WALKTHROUGH.md](FIRST_SEARCH_WALKTHROUGH.md).
