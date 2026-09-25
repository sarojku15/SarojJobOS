#!/usr/bin/env python3

"""
Apna search-page parsing (Phase 12).

Apna's job-search page (https://apna.co/jobs?...) is a Next.js
App-Router page. Its job listings are NOT reachable via a
classic `__NEXT_DATA__` blob or a separate XHR/fetch call (a live
network-request capture this phase found only autocomplete/analytics
XHRs, no job-listing API call) -- they are embedded directly in the
initial server-rendered HTML as a React Server Components (RSC)
streaming payload: a sequence of

    <script>self.__next_f.push([N,"<escaped JSON-ish string>"])</script>

tags whose string arguments concatenate (in order) into one large RSC
protocol buffer. Confirmed this phase via a controlled live fetch
(data/reports/phase12_public_source_expansion.md) that a PLAIN,
unauthenticated HTTP GET (no Playwright/browser rendering needed)
already contains this payload -- i.e. this is genuinely server-side
rendered data on the first response, not a client-side-only fetch.

This module reassembles that buffer and extracts the one JSON object
named "initialSSRJobFeedData" from it via balanced-brace scanning (the
RSC protocol is not itself pure JSON, so a full-document json.loads()
is not possible -- this targeted extraction is the narrowest approach
that still avoids brittle DOM/CSS scraping, per this phase's explicit
"prefer structured public data over pixel/DOM scraping" instruction).

Every field extracted here is grounded in what was actually observed
in a real, live-captured page (data/reports/apna_phase12_captures/) --
nothing is invented. No posted-date field exists anywhere in the
observed job objects; posted_date is therefore always left empty here
(never guessed), and freshness classification for Apna jobs will
honestly resolve to UNKNOWN as a result -- an accurate reflection of
a real data-availability gap, not a parser bug.
"""

import json
import re

_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[\d+,(".*?")\]\)</script>', re.DOTALL)

_BASE_URL = "https://apna.co"


def reassemble_next_f_buffer(html):
    """Concatenate every self.__next_f.push([N, "chunk"]) chunk, in the
    order they appear, into one buffer. Each chunk is a proper JSON
    string literal (json.loads()-able on its own); a chunk that fails
    to decode is skipped rather than aborting the whole page (a
    single malformed chunk must not lose every other chunk's data)."""
    buffer_parts = []
    for match in _PUSH_RE.finditer(html):
        try:
            buffer_parts.append(json.loads(match.group(1)))
        except (json.JSONDecodeError, ValueError):
            continue
    return "".join(buffer_parts)


def _extract_balanced_json_object(text, key):
    """Find `"<key>":` in `text` and return the balanced `{...}` JSON
    object that immediately follows it, respecting quoted strings and
    escape sequences. Returns None if the key or a balanced object is
    not found -- never a truncated/malformed guess."""
    marker = f'"{key}":'
    key_index = text.find(marker)
    if key_index == -1:
        return None

    start = text.find("{", key_index)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]

    return None  # never closed -- malformed/truncated, fail closed


def extract_job_feed(html):
    """
    Returns the parsed `initialSSRJobFeedData` dict (with
    `jobsList`/`totalJobsCount`/etc.) or None if this page's RSC
    payload does not contain one (e.g. a genuinely empty search, or
    the page structure has changed).
    """
    buffer = reassemble_next_f_buffer(html)
    raw = _extract_balanced_json_object(buffer, "initialSSRJobFeedData")
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def find_next_page_url(html, current_url):
    """
    Apna's RSC payload embeds a pagination link as
    {"rel":"next","href":"https://apna.co/jobs?page=N"} (confirmed via
    live capture this phase). Returns the absolute URL, or None if
    absent or pointing off apna.co.
    """
    buffer = reassemble_next_f_buffer(html)
    match = re.search(r'"rel":"next","href":"(https://apna\.co/[^"]+)"', buffer)
    if not match:
        return None
    href = match.group(1).replace("\\/", "/")
    return href


_EXPERIENCE_TAG_RE = re.compile(r"Min\.\s*(\d+)\s*years?", re.IGNORECASE)


def _parse_job_entry(entry):
    """
    Extract a raw-job-shaped dict (the shape
    discover_local.normalize_job() expects) from one jobsList entry.
    Returns None if title or company cannot be resolved (fails closed
    -- never fabricates either).
    """
    data = entry.get("data") if isinstance(entry, dict) else None
    if not isinstance(data, dict):
        return None

    title = data.get("jobTitle")
    if not isinstance(title, str) or not title.strip():
        return None

    org = data.get("jobOrganisationDetails") or {}
    company = org.get("organisationName") if isinstance(org, dict) else None
    if not isinstance(company, str) or not company.strip():
        return None

    public_url = data.get("jobPublicURL")
    job_url = f"{_BASE_URL}{public_url}" if isinstance(public_url, str) and public_url.startswith("/") else (
        public_url if isinstance(public_url, str) and public_url.startswith("http") else ""
    )

    location = data.get("jobCardAddress")
    location = location.strip() if isinstance(location, str) else ""

    tags = data.get("jobUITags") or []
    tag_labels = [t.get("tagLabel", "") for t in tags if isinstance(t, dict)]

    work_model = ""
    employment_type = ""
    experience_required = ""
    for label in tag_labels:
        lower = label.lower()
        if "work from" in lower or "remote" in lower or "hybrid" in lower:
            work_model = label
        elif "full time" in lower or "part time" in lower or "contract" in lower or "intern" in lower:
            employment_type = label
        else:
            exp_match = _EXPERIENCE_TAG_RE.search(label)
            if exp_match:
                experience_required = label

    return {
        "source": "APNA",
        "company": company.strip(),
        "title": title.strip(),
        "location": location,
        "work_model": work_model,
        "job_url": job_url,
        "application_url": job_url,
        "posted_date": "",  # never observed in this data -- never guessed
        "jd_text": "",  # not present in the listing feed; a detail page would be needed and is not fetched here
        "experience_required": experience_required,
        "mandatory_skills": [],
        "preferred_skills": [],
        "_employment_type": employment_type,  # informational only, not part of the existing normalize_job() shape
    }


def parse_job_feed_entries(job_feed):
    """job_feed: the dict returned by extract_job_feed(). Returns a
    list of raw-job dicts, skipping (never fabricating) any entry
    missing a resolvable title/company/URL."""
    if not isinstance(job_feed, dict):
        return []

    entries = job_feed.get("jobsList") or []
    jobs = []
    for entry in entries:
        job = _parse_job_entry(entry)
        if job is not None and job["job_url"]:
            job.pop("_employment_type", None)
            jobs.append(job)
    return jobs


# ---------------------------------------------------------------------
# Detail-page parsing (Phase: APNA detail-page fetching)
# ---------------------------------------------------------------------

_JOB_SCHEMA_LD_JSON_PATTERN = re.compile(
    r'id="jdp-job-schema"\s+type="application/ld\+json">(.*?)</script>', re.DOTALL
)


def extract_job_posting_schema(html):
    """
    A job detail page's own schema.org JobPosting JSON-LD block --
    <script id="jdp-job-schema" type="application/ld+json">{...}
    -- confirmed present (live capture) on every real detail page
    checked in this phase, including externally-aggregated listings
    whose full description is otherwise only reachable via an
    unresolved React Server Components chunk reference elsewhere on
    the page (deliberately NOT attempted -- resolving RSC $N chunk
    references would require implementing React's Flight streaming
    protocol, and a subtly-wrong implementation risks silently
    fabricating/garbling JD text, which this project must never do;
    this clean, standard, already-resolved JSON-LD block is used
    instead). Returns the parsed dict, or None if the block is absent
    or not valid JSON -- never a partial/guessed structure.
    """
    match = _JOB_SCHEMA_LD_JSON_PATTERN.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def parse_detail_page(html, job_url):
    """
    Extract detail-page enrichment fields for one already-known job
    (job_url is the caller's own listing URL, used only to tag which
    job this enrichment belongs to -- never re-derived from the page).

    Returns a dict with ONLY the keys that were reliably, genuinely
    present on the page: any subset of {jd_text, posted_date,
    experience_required, experience_min_months, location}. A field
    whose source data is missing/unparseable is simply absent from the
    returned dict (never set to a guessed/empty placeholder) -- the
    caller (apna_adapter.py) is responsible for only ever using a
    present key to fill in a listing job's OWN missing/less-precise
    value, never to overwrite a non-empty listing value.

    Returns {} (not None) if no JobPosting schema block was found at
    all -- a clean, explicit "nothing reliably extractable here",
    distinct from a network/fetch failure (which the adapter handles
    separately, before this function is ever called).
    """
    posting = extract_job_posting_schema(html)
    if not isinstance(posting, dict):
        return {}

    enrichment = {}

    description = posting.get("description")
    if isinstance(description, str) and description.strip():
        enrichment["jd_text"] = description.strip()

    date_posted = posting.get("datePosted")
    if isinstance(date_posted, str) and date_posted.strip():
        # "2026-07-26T00:00:00.000Z" -- freshness.classify_freshness()'s
        # ISO pattern matches the leading YYYY-MM-DD substring directly;
        # sliced here only for a cleaner stored value, not reformatted.
        enrichment["posted_date"] = date_posted.strip()[:10]

    experience_requirements = posting.get("experienceRequirements")
    if isinstance(experience_requirements, dict):
        months_raw = experience_requirements.get("monthsOfExperience")
        try:
            months = int(months_raw) if months_raw is not None else None
        except (TypeError, ValueError):
            months = None
        if months is not None:
            enrichment["experience_min_months"] = months
            years = months / 12
            years_text = f"{years:.0f}" if years == int(years) else f"{years:.1f}"
            unit = "year" if years_text == "1" else "years"
            enrichment["experience_required"] = f"Min. {years_text} {unit}"

    job_location = posting.get("jobLocation")
    if isinstance(job_location, dict):
        address = job_location.get("address")
        if isinstance(address, dict):
            locality = address.get("addressLocality")
            if isinstance(locality, str) and locality.strip():
                enrichment["location"] = locality.strip()

    return enrichment
