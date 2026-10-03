# Data & Backup

For *where* each database/port lives and the dev-vs-production
distinction, [CONFIGURATION.md](CONFIGURATION.md) is authoritative —
this page covers backup, restore, and moving your data.

## What's on disk

| What | Location (relative to the project folder) | Git-tracked? |
|---|---|---|
| Development database | `data/applications/jobos_dev.db` | No (gitignored) |
| Production/automation database | `data/applications/jobos.db` | No (gitignored) |
| Uploaded resumes | `data/applications/resumes_dev/` (dev) or `resumes/` (production) | No (gitignored) |
| Generated reports/exports | `data/reports/`, `data/daily/` | Mixed — see `.gitignore`; your own generated exports are not tracked |
| Your own local provider keys | `.env` (project root) | No (gitignored) |
| Your own legacy-CLI profile (optional, advanced) | `config/profile.json` | No (gitignored — `config/profile.json.example` is the tracked template) |
| n8n's own data (workflows, credentials, execution history) | Inside the `n8n` Docker container's own volume, not this project folder | No |

Everything containing your personal data lives outside git. A fresh
clone of this repository starts with **none of it** — zero candidates,
zero resumes, no keys.

## Development vs. production/local-production

- **Development** (`jobos_dev.db`) — the normal interactive server you
  use while trying things out.
- **Production/automation** (`jobos.db`) — a separate process/database
  intended for your real, ongoing data and anything n8n/cron drives.
  "Production" here means *your own* real local instance, not a
  hosted service — JobOS has no cloud component.

Full detail on both: [CONFIGURATION.md](CONFIGURATION.md).

## Backing up

Copy the relevant files while JobOS is **not actively writing** to
them (stop the API process, or at minimum avoid mid-write):

```bash
# macOS/Linux
cp data/applications/jobos.db ~/jobos-backup-$(date +%Y%m%d).db
cp -r data/applications/resumes ~/jobos-resumes-backup-$(date +%Y%m%d)/

# Windows PowerShell
Copy-Item data\applications\jobos.db "$HOME\jobos-backup-$(Get-Date -Format yyyyMMdd).db"
Copy-Item -Recurse data\applications\resumes "$HOME\jobos-resumes-backup-$(Get-Date -Format yyyyMMdd)"
```

Back up `.env` too if you've configured provider keys — treat that
backup file with the same care as the original (it contains your real
keys).

## Restoring

Copy a backed-up `.db` file back to its original path, replacing the
current one. Restart the JobOS process afterward.

## Moving to another computer / another OS

1. Copy the whole `data/` folder (database + resumes + reports) and
   your `.env` (if used) to the new machine.
2. Clone this repository fresh on the new machine and follow
   [GETTING_STARTED.md](GETTING_STARTED.md)'s install steps for that
   OS.
3. Place your copied `data/` folder and `.env` into the new clone's
   project root, in the same relative locations shown in the table
   above.
4. Start JobOS as normal — it's an ordinary SQLite file and plain
   PDFs, so this works identically moving between macOS, Linux, and
   Windows.

## n8n data

n8n's workflows, credentials, and execution history live in the n8n
Docker container's own volume — separate from JobOS's own `data/`
folder. See [N8N_SETUP.md](N8N_SETUP.md) §20 for backing up your n8n
workflow configuration specifically.

## Protecting personal data

Never commit `.env`, any `.db` file, or anything under `resumes/`/
`resumes_dev/` — `.gitignore` already excludes all of these by
default; don't `git add -f` them. See [SECURITY.md](SECURITY.md) for
the full privacy/security model.
