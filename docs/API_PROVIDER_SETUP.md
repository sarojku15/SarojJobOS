# Search Provider API Keys

JobOS works out of the box with **zero API keys** — four job boards
(Naukri, Hirist, IIMJobs, Apna) are crawled directly and need nothing
from you. Provider API keys are **optional** and only unlock seven
additional boards (LinkedIn, Indeed, Foundit, Instahyre, Cutshort,
Wellfound, Shine) that JobOS cannot crawl directly, so it discovers
them through a third-party search API instead.

**Every key you add is YOUR OWN key, from YOUR OWN account on that
provider's site.** JobOS ships with no key of any kind, requires none
to start, and nothing in this repository ever reads a key belonging to
anyone but you. There is no shared, demo, fallback, or default key —
if you don't add one, the 7 provider-backed boards simply stay
unconfigured and JobOS keeps working on the 4 direct sources.

## The normal workflow

```
You create an account on a provider's site
          ↓
You generate YOUR OWN API key on that site
          ↓
JobOS → Settings → Search Providers
          ↓
You paste YOUR key into the field for that provider
          ↓
Click "Test"
          ↓
Click "Enable"
          ↓
Create/run a search — the 7 extra boards are now included
```

You never need to edit `.bashrc`, `.zshrc`, a PowerShell profile,
macOS Keychain, Windows Credential Manager, or any OS-level secret
store to use this. (An advanced, optional alternative — editing a
local `.env` file directly — is covered at the bottom of this page for
developers who prefer it.)

## Supported providers

JobOS supports exactly five search-provider integrations
(`scripts/search_provider.py`). Configuring **any one** of them
unlocks all 7 restricted boards — you do not need all five.

| Provider | Env var JobOS writes | Where to get a key |
|---|---|---|
| You.com | `YOU_API_KEY` | https://api.you.com |
| Tavily | `TAVILY_API_KEY` | https://tavily.com |
| Exa | `EXA_API_KEY` | https://exa.ai |
| Brave Search | `BRAVE_API_KEY` | https://brave.com/search/api/ |
| Serper | `SERPER_API_KEY` | https://serper.dev |

These URLs are each provider's own site at the time of writing — if a
provider has since changed its signup/API page, search for "<provider
name> API key" to find the current one. JobOS does not control or
guarantee these external sites.

For each provider, the general steps are the same shape:
1. Create a free or trial account on the provider's own site.
2. Find their "API Keys" or "Dashboard" section (every one of these
   five providers has a developer dashboard once logged in).
3. Generate a new key and copy it — you'll paste it into JobOS next.

## Adding a key inside JobOS

1. Open JobOS in your browser and go to **Settings → Search
   Providers** (`/settings/search-providers`).
2. Find the provider you just created a key for.
3. Paste your key into its field and save. JobOS never shows the full
   key back to you after saving — only a masked form like
   `••••••••1234` (the last 4 characters), confirming it saved
   correctly without ever re-displaying the secret.
4. Click **Test** for that provider. This makes one real call to
   confirm the key actually works — it tells you success, an
   authentication failure, or a rate-limit response, never a fake
   "it worked."
5. Click **Enable**. The provider is now active; the 7 provider-backed
   boards will be included in future searches.

## Replacing or removing a key

- **Replace**: paste a new key over the old one and save — the old
  value is overwritten, never kept.
- **Remove**: use the Remove/Delete action for that provider. The key
  is deleted from storage; the provider reverts to unconfigured, and
  the 7 provider-backed boards fall back to being skipped the way they
  were before any key existed.
- **Disable** (without removing): turns the provider off without
  deleting the key, so you can re-enable it later without re-entering
  it.

## What each state actually means

| Situation | What happens |
|---|---|
| No key configured for any provider | The 7 provider-backed boards are reported `SEARCH_PROVIDER_NOT_CONFIGURED` / skipped. The 4 direct boards still run normally. Nothing fails or errors because of this — it's a normal, supported state. |
| Key configured, valid | The 7 boards report `AVAILABLE_VIA_SEARCH_PROVIDER` and are searched through that provider. |
| Key invalid/revoked | The **Test** action reports the authentication failure clearly. A live search run's source audit records that provider's attempt as `FAILED`, not silently as zero results. |
| Rate-limited | JobOS's provider layer fails over to the next configured provider (if you have more than one) only on a real failure signal (auth/quota/rate-limit/timeout/network/server error) — never because a search simply returned nothing. If no other provider is configured, that run's source audit records the rate-limit as the failure reason. |
| Provider returns zero results for a query | Reported as `ZERO` in the source audit — a legitimate empty result is never confused with a failure. |

## Where keys are actually stored

Keys are written to a local `.env` file in the project root (via
`scripts/env_config.py`) — the same file the rest of JobOS already
treats as local, optional, developer-style configuration. This file is
**gitignored** (never committed, never pushed) and is never bundled
into reports, exports, logs, or any API response. See
[SECURITY.md](SECURITY.md) for the full data-handling picture.

### Advanced/developer alternative: editing `.env` directly

If you prefer not to use the Settings page, you can instead copy
`config/jobos.env.example` to `.env` in the project root and set the
variables yourself:

```bash
cp config/jobos.env.example .env
# then edit .env and set, e.g.:
# YOU_API_KEY=<YOUR_PROVIDER_API_KEY>
```

This is equivalent to using the Settings page — JobOS reads whichever
value is currently set, regardless of which way it got there. Restart
the JobOS process after editing `.env` by hand (the Settings page
updates the running process immediately; a manually-edited file is
only picked up on the next startup).
