# Quality Rules (Phase J)

Applied by `scripts/validate_discovered_job.py` to EVERY raw job dict
from EVERY acquisition mechanism, before it is treated as an
actionable job anywhere downstream. These rules are stricter for
`WEB_SEARCH`-sourced jobs (search-result snippets are not verified
pages) but apply uniformly -- there is one validator, not one per
source.

## Required for any job to be accepted

- `title`: non-empty string.
- `company`: non-empty string.
- `source`: non-empty string, must be a source name known to
  `source_registry.list_sources()`.
- `job_url` OR `application_url`: at least one non-empty, syntactically
  valid (`http(s)://...`) URL. **A result with neither is rejected --
  it is never presented as an actionable job** (Phase C rule, restated
  here).

## Never fabricated -- left empty/None/"UNKNOWN" if not actually observed

- `salary` (min/max/currency)
- `experience_required`
- `posted_date`
- `location`
- `jd_text` (a search-result snippet is NOT a full JD -- store what was
  actually returned, never pad or invent additional description text)

## Discovery vs. posted timestamps

`discovered_at` (when THIS system found the job) and `posted_date`
(when the source says the job was posted, if it says) are always kept
distinct -- never collapse "found today" into "posted today". A
`WEB_SEARCH` result that gives no posted date keeps `posted_date`
empty; `freshness.py`'s existing classification already handles a
missing/unknown posted date honestly (never treats "unknown" as
"fresh").

## Duplicate / dead-link handling

- Validation happens BEFORE `cross_source_dedup.py` runs -- a malformed
  record (missing title/company/URL) is dropped here so dedup never has
  to reason about it.
- A URL that 404s or redirects to a generic "job no longer available"
  page is a per-source concern (an adapter/provider that can cheaply
  detect this should mark the job `flagged_stale=True` in its own raw
  dict's `extra`, but MUST NOT silently drop it -- a human deciding
  "this looks dead" is safer than this Skill guessing wrong). No
  adapter in this project currently implements dead-link detection;
  this is a documented gap, not a fabricated capability.

## Malformed records

`validate_discovered_job.py` returns a `(valid: bool, reasons: list[str])`
tuple, never raises for a single bad record -- one malformed job must
never abort discovery of the other N-1. The caller (`discover_jobs.py`)
collects rejected records under `errors`, with the reason, for
observability -- never a silent drop with no trace.
