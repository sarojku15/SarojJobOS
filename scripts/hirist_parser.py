#!/usr/bin/env python3

"""
Hirist search-page parsing: schema.org JSON-LD extraction, defensive
classification, and pagination-link discovery. Pure, offline logic
only -- no network access, no browser, no live fetch of any kind.
Mirrors naukri_parser.py's separation of parsing/classification from
fetching (hirist_fetcher.py) and orchestration (hirist_adapter.py),
applied fresh for Hirist's very different (JSON-LD-based, not
HTML-card-based) page structure -- the job-card/ItemList parsing logic
in this file is independently written for Hirist's own structure, not
copied from Naukri.

============================================================================
BLOCK/CHALLENGE DETECTION REUSES naukri_parser._extract_visible_text()
(Phase 6 Step 6 remediation)
============================================================================
_find_block_signal() below deliberately imports and reuses
naukri_parser.py's existing, already-proven _extract_visible_text()
utility -- a genuinely source-agnostic HTML->visible-text converter
(it knows nothing about Naukri's page structure; it only knows how to
skip <script>/<style> element content and tag markup). This closes a
real, demonstrated false-positive class: checking phrases against the
FULL raw HTML (as this function originally did) matches dormant CSS
class names, <script src> attributes, and JSON-LD payload text just as
readily as genuine visible page content -- exactly the failure mode
naukri_parser.py's own docstrings already document from a real prior
incident on a real captured Naukri page (the literal word "captcha"
inside a dormant .bot-guard-captcha CSS class), and exactly the failure
mode data/reports/hirist_block_classifier_forensics.md diagnosed for
Hirist's own first live validation attempt. See that report and
data/reports/hirist_block_classifier_remediation.md for the full
evidence trail. naukri_parser.py itself is NOT modified by this reuse.

============================================================================
STRICT EVIDENCE DISCIPLINE (Phase 6 Step 3)
============================================================================
Every field this module extracts is grounded in one of exactly two
sources:

  1. Phase 6 Step 1's actual live observation (data/reports/
     hirist_phase6_inspection.md): a schema.org "ItemList" JSON-LD
     block, "numberOfItems", a per-item "position", a partial "name"
     string (e.g. "Verint - Senior ..."), and a directly-observed
     <link rel="next" href="...?page=2"> pagination tag. Nothing about
     a "detail page", "application URL", native freshness/remote
     filtering, or salary/experience data was ever observed -- none of
     that is implemented here.

  2. schema.org's own real, external, published JobPosting/ItemList
     vocabulary (title, hiringOrganization.name, url, datePosted,
     description, employmentType, baseSalary, jobLocation) -- applied
     defensively, reading a field ONLY if it is actually present in
     the parsed data, never assumed. This path was NOT directly
     confirmed against the real Hirist page in Phase 6 Steps 1-8 (the
     one capture available then was truncated); Phase 6 Step 9's full,
     untruncated live capture (data/reports/hirist_step9_raw_capture.html)
     subsequently confirmed this nested-object shape is NEVER used by
     real Hirist search-results data (0/20 real entries had a nested
     "item" object) -- this path remains implemented, unmodified, for
     defensive/standards-compliance reasons only, and is exercised
     solely by this module's own synthetic fixtures
     (data/fixtures/hirist/).

  3. Phase 6 Step 9's full, untruncated live capture (data/reports/
     hirist_raw_payload_capture.md / hirist_step9_raw_capture.html):
     confirmed every one of 20 real ListItem entries carries a
     TOP-LEVEL "url" field (sibling to "name" and "position", e.g.
     "https://www.hirist.tech/j/verint-senior-dev-ops-engineer-1672606")
     -- and confirmed "@id", "sameAs", "jobLocation",
     "experienceRequirements", "baseSalary", "description", and any
     application/contact field are absent from every real entry. Phase
     6 Step 10 added the one field this evidence justifies: reading
     this top-level "url" on the flat-name path (see
     _parse_job_entry() below). It did NOT justify, and this module
     still does NOT implement, any company/title parsing change --
     Step 9's same capture also confirmed the "name" field mixes at
     least three different conventions (bare title; "Company - Title";
     "Company - Title - Tags") with no deterministic rule to
     distinguish them, which remains a documented, unresolved
     limitation (data/reports/hirist_job_url_remediation.md).

The flat "name" string company/title split (the ONLY field Phase 6
directly, unambiguously observed) is applied ONLY when the exact
delimiter " - " occurs exactly once in that string. If it occurs zero
or more-than-one times, the entry is skipped entirely (fails closed)
rather than guessing a split point -- per this project's explicit
"do not assume a specific title delimiter" instruction. A skipped entry
is never fabricated with a placeholder company/title.
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urljoin, urlparse

# Reused, unmodified, from naukri_parser.py -- see module docstring's
# "BLOCK/CHALLENGE DETECTION" section above for why. naukri_parser.py
# is not edited by this import.
from naukri_parser import _extract_visible_text


class HiristPageState(Enum):
    VALID_RESULTS = "VALID_RESULTS"
    VALID_EMPTY_RESULT = "VALID_EMPTY_RESULT"
    BLOCKED = "BLOCKED"
    SOFT_BLOCK_OR_CHALLENGE = "SOFT_BLOCK_OR_CHALLENGE"
    PARSE_FAILURE = "PARSE_FAILURE"


@dataclass
class HiristSearchClassification:
    state: HiristPageState
    jobs: list = field(default_factory=list)
    next_url: str | None = None
    detail: str = ""
    matched_block_phrase: str | None = None


_JSON_LD_SCRIPT_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

# Two attribute orderings handled defensively -- real-world HTML is not
# guaranteed to always write rel before href.
_NEXT_LINK_RE = re.compile(
    r'<link[^>]+rel=["\']next["\'][^>]+href=["\']([^"\']+)["\']', re.IGNORECASE
)
_NEXT_LINK_RE_ALT = re.compile(
    r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']next["\']', re.IGNORECASE
)

# Phase 6 observed no block of any kind, so none of these phrases were
# directly confirmed against Hirist specifically -- they mirror the
# same generic, widely-applicable phrase categories already used for
# Naukri/LinkedIn inspections, kept separate from any Naukri-specific
# list per this module's own docstring.
_HARD_BLOCK_PHRASES = ("access denied", "request blocked", "403 forbidden")
_CHALLENGE_PHRASES = (
    "captcha",
    "checkpoint",
    "security check",
    "unusual activity",
    "verify you are a human",
    "are you a robot",
)

# The only host Phase 6 ever observed or requested against -- a
# discovered rel="next" link is never followed off this host.
_ALLOWED_NEXT_URL_HOST = "www.hirist.tech"

_NAME_DELIMITER = " - "


def _find_block_signal(html: str):
    """
    Checks _HARD_BLOCK_PHRASES/_CHALLENGE_PHRASES against extracted
    VISIBLE TEXT (title + body, via naukri_parser._extract_visible_text())
    -- never against the raw HTML string. This is the Phase 6 Step 6
    remediation: checking raw HTML let a phrase like "captcha" match a
    dormant CSS class name, a <script src="...recaptcha..."> attribute,
    or JSON-LD payload text, none of which a human visitor ever sees
    rendered -- see module docstring and
    data/reports/hirist_block_classifier_remediation.md.
    """
    title, visible_text = _extract_visible_text(html)
    combined = f"{title} {visible_text}".lower()

    for phrase in _HARD_BLOCK_PHRASES:
        if phrase in combined:
            return ("BLOCKED", phrase)
    for phrase in _CHALLENGE_PHRASES:
        if phrase in combined:
            return ("SOFT_BLOCK_OR_CHALLENGE", phrase)
    return (None, None)


def _find_item_list_json_ld(html: str):
    """
    Scan every <script type="application/ld+json"> block for one whose
    parsed JSON has "@type" == "ItemList" (the exact type Phase 6
    directly observed). Returns the parsed dict, or None if no such
    block exists at all. Raises ValueError if at least one JSON-LD
    block was present but none of them parsed as valid JSON -- kept
    distinct from "no block at all" so the caller can classify
    PARSE_FAILURE precisely instead of conflating "malformed" with
    "absent".
    """
    blocks = _JSON_LD_SCRIPT_RE.findall(html)
    if not blocks:
        return None

    malformed_seen = False
    for raw in blocks:
        try:
            parsed = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            malformed_seen = True
            continue

        if isinstance(parsed, dict) and parsed.get("@type") == "ItemList":
            return parsed

    if malformed_seen:
        raise ValueError("one or more JSON-LD blocks were present but not valid JSON")

    return None


def _sane_url_or_none(value):
    """
    Defensive URL sanity check: accepts only a well-formed http(s) URL.
    A present-but-malformed url field never invalidates the whole
    entry -- it is simply left unset (empty string), matching this
    project's existing "never fabricate, leave unknown fields blank"
    discipline.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return value.strip()
    return None


def _extract_job_location_text(job_location):
    """
    schema.org JobPosting.jobLocation is conventionally a Place object,
    optionally with an address. Reads only an unambiguous plain-text
    representation if present; never fabricates a location string.
    """
    if isinstance(job_location, str):
        return job_location.strip() or None
    if isinstance(job_location, dict):
        address = job_location.get("address")
        if isinstance(address, str):
            return address.strip() or None
        if isinstance(address, dict):
            locality = address.get("addressLocality")
            if isinstance(locality, str) and locality.strip():
                return locality.strip()
    return None


def _empty_raw_job(company, title):
    return {
        "company": company,
        "title": title,
        "location": "",
        "work_model": "",
        "job_url": "",
        "application_url": "",
        "posted_date": "",
        "jd_text": "",
        "experience_required": "",
        "mandatory_skills": [],
        "preferred_skills": [],
    }


def _parse_job_entry(list_item):
    """
    Extract a raw-job-shaped dict (the exact shape
    discover_local.normalize_job() expects) from one itemListElement
    entry, or None if this entry cannot be safely parsed into the
    REQUIRED company+title shape without guessing.

    Two extraction paths, in preference order -- see this module's
    docstring for the evidence basis of each:

      1. A nested "item" object (schema.org's standard ItemList ->
         Thing/JobPosting shape). If present but missing either a
         resolvable title or company, the entry is skipped rather than
         falling back to a guessed flat-name split on a MIXED-shape
         entry.
      2. A flat "name" string, split on " - " ONLY when that exact
         delimiter occurs exactly once.

    Phase 6 Step 9 job_url remediation: a real, live-captured Hirist
    page (data/reports/hirist_step9_raw_capture.html) confirmed every
    one of 20 real ListItem entries carries a TOP-LEVEL "url" field,
    sibling to "name" -- never nested inside an "item" object (0/20
    entries had one). The flat-name path below now reads this
    top-level "url" the same way the nested path already reads
    item.url -- via the same _sane_url_or_none() validator, with no
    new or different validation rule. This does NOT touch the
    company/title split logic at all (still the same "exactly one
    delimiter occurrence" fail-closed rule) -- see
    data/reports/hirist_job_url_remediation.md for the full evidence
    trail and for why the company/title ambiguity itself is
    deliberately NOT addressed here.
    """
    if not isinstance(list_item, dict):
        return None

    nested_item = list_item.get("item")
    if isinstance(nested_item, dict):
        title = nested_item.get("title") or nested_item.get("name")
        hiring_org = nested_item.get("hiringOrganization")
        company = None
        if isinstance(hiring_org, dict):
            company = hiring_org.get("name")
        elif isinstance(hiring_org, str):
            company = hiring_org

        if not (isinstance(title, str) and title.strip() and isinstance(company, str) and company.strip()):
            return None

        job = _empty_raw_job(company.strip(), title.strip())

        url = _sane_url_or_none(nested_item.get("url"))
        if url:
            job["job_url"] = url

        date_posted = nested_item.get("datePosted")
        if isinstance(date_posted, str) and date_posted.strip():
            job["posted_date"] = date_posted.strip()

        description = nested_item.get("description")
        if isinstance(description, str) and description.strip():
            job["jd_text"] = description.strip()

        employment_type = nested_item.get("employmentType")
        if isinstance(employment_type, str) and employment_type.strip():
            job["work_model"] = employment_type.strip()

        location_text = _extract_job_location_text(nested_item.get("jobLocation"))
        if location_text:
            job["location"] = location_text

        return job

    # No nested item object -- fall back to the one flat field Phase 6
    # directly observed.
    name = list_item.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    occurrences = name.count(_NAME_DELIMITER)
    if occurrences != 1:
        return None  # fails closed -- ambiguous or absent delimiter, never guessed

    company_part, title_part = name.split(_NAME_DELIMITER, 1)
    company_part = company_part.strip()
    title_part = title_part.strip()
    if not company_part or not title_part:
        return None

    job = _empty_raw_job(company_part, title_part)

    # Phase 6 Step 9/10: top-level ListItem.url, sibling to "name" --
    # the shape every real captured entry actually uses. Same
    # validator as the nested-item path above; never constructs or
    # guesses a URL -- left empty if absent or malformed.
    url = _sane_url_or_none(list_item.get("url"))
    if url:
        job["job_url"] = url

    return job


def _parse_job_entry_title_only(list_item):
    """
    Phase 11 addition: extract ONLY title+job_url from one
    itemListElement, WITHOUT attempting the flat-name company/title
    split _parse_job_entry() performs. Used by the new two-stage
    pipeline (listing page for title/url -> detail page for a
    RELIABLE company name via parse_job_detail_json_ld()'s
    hiringOrganization.name, never a string-split guess).

    Never returns a company field at all -- callers must resolve it
    via a detail-page fetch. Returns None only if title or job_url
    cannot be resolved at all (still fails closed on a genuinely
    unusable entry).
    """
    if not isinstance(list_item, dict):
        return None

    nested_item = list_item.get("item")
    if isinstance(nested_item, dict):
        title = nested_item.get("title") or nested_item.get("name")
        url = _sane_url_or_none(nested_item.get("url"))
        if isinstance(title, str) and title.strip() and url:
            return {"title": title.strip(), "job_url": url}
        return None

    name = list_item.get("name")
    url = _sane_url_or_none(list_item.get("url"))
    if isinstance(name, str) and name.strip() and url:
        return {"title": name.strip(), "job_url": url}
    return None


def parse_job_detail_json_ld(html):
    """
    Phase 11 fix for the company/title ambiguity documented throughout
    this module's history (see docstring point 3 and
    data/reports/hirist_job_url_remediation.md): a controlled live
    validation (data/reports/phase11_public_multisource_completion.md)
    confirmed Hirist's job DETAIL page (not the listing page) carries a
    full schema.org JobPosting JSON-LD block with a reliable, never-
    ambiguous hiringOrganization.name -- validated against 10 real
    detail pages, 10/10 correct, including one genuinely
    company-anonymized listing ("Verified Company", Hirist's own
    honest placeholder -- preserved as-is, never overridden) and one
    listing whose name had zero " - " delimiters at all (proving the
    old flat-name-split approach could never have recovered its
    company under any heuristic).

    Returns a dict {title, company, location, posted_date,
    employment_type, jd_text} or None if no JobPosting JSON-LD block is
    present/parseable -- fails closed, never fabricates a company from
    a title string.
    """
    try:
        blocks = _JSON_LD_SCRIPT_RE.findall(html)
    except TypeError:
        return None

    for raw in blocks:
        try:
            data = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            continue

        if not (isinstance(data, dict) and data.get("@type") == "JobPosting"):
            continue

        hiring_org = data.get("hiringOrganization")
        company = None
        if isinstance(hiring_org, dict):
            name = hiring_org.get("name")
            if isinstance(name, str) and name.strip():
                company = name.strip()
        elif isinstance(hiring_org, str) and hiring_org.strip():
            company = hiring_org.strip()

        if not company:
            return None  # No JobPosting without a resolvable company is used -- fails closed.

        title = data.get("title")
        title = title.strip() if isinstance(title, str) and title.strip() else None

        location_text = _extract_job_location_text(data.get("jobLocation"))
        if location_text is None and isinstance(data.get("jobLocation"), list) and data["jobLocation"]:
            location_text = _extract_job_location_text(data["jobLocation"][0])

        posted_date = data.get("datePosted")
        posted_date = posted_date.strip() if isinstance(posted_date, str) and posted_date.strip() else ""

        employment_type = data.get("employmentType")
        employment_type = employment_type.strip() if isinstance(employment_type, str) and employment_type.strip() else ""

        description = data.get("description")
        description = description.strip() if isinstance(description, str) and description.strip() else ""

        return {
            "title": title,
            "company": company,
            "location": location_text or "",
            "posted_date": posted_date,
            "employment_type": employment_type,
            "jd_text": description,
        }

    return None


def _find_next_page_url(html, current_url, allowed_host=_ALLOWED_NEXT_URL_HOST):
    """
    Look for the directly-observed <link rel="next" href="..."> tag.
    Resolves a relative URL against current_url. Returns None (no next
    page) if absent, malformed, or pointing off `allowed_host` (default:
    www.hirist.tech, unchanged for every existing caller) -- a
    deliberate safety check: never follow a discovered "next" link to a
    different host.

    allowed_host is an additive Phase 11 parameter -- iimjobs_parser.py
    passes its own host, since it shares this module's generic
    schema.org parsing logic but is a different site.
    """
    match = _NEXT_LINK_RE.search(html) or _NEXT_LINK_RE_ALT.search(html)
    if not match:
        return None

    href = match.group(1).strip()
    if not href:
        return None

    resolved = urljoin(current_url, href)
    parsed = urlparse(resolved)

    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.netloc and parsed.netloc != allowed_host:
        return None

    return resolved


def classify_search_page(html, current_url, entry_parser=_parse_job_entry, allowed_next_host=_ALLOWED_NEXT_URL_HOST):
    """
    Classify one already-fetched Hirist search-results page. Pure
    function -- no network access, no side effects.

    entry_parser is an additive Phase 11 hook (default: unchanged
    behavior, _parse_job_entry, the original flat-name-split logic) --
    pass _parse_job_entry_title_only to get title+job_url-only entries
    for the two-stage (listing + detail-page) pipeline HiristAdapter
    now uses. Every existing call site/test that does not pass this
    argument is completely unaffected.
    """
    if not html or not html.strip():
        return HiristSearchClassification(state=HiristPageState.PARSE_FAILURE, detail="empty response body")

    block_kind, block_phrase = _find_block_signal(html)
    if block_kind == "BLOCKED":
        return HiristSearchClassification(
            state=HiristPageState.BLOCKED,
            detail=f"block phrase matched: {block_phrase!r}",
            matched_block_phrase=block_phrase,
        )
    if block_kind == "SOFT_BLOCK_OR_CHALLENGE":
        return HiristSearchClassification(
            state=HiristPageState.SOFT_BLOCK_OR_CHALLENGE,
            detail=f"challenge phrase matched: {block_phrase!r}",
            matched_block_phrase=block_phrase,
        )

    try:
        item_list = _find_item_list_json_ld(html)
    except ValueError as error:
        return HiristSearchClassification(state=HiristPageState.PARSE_FAILURE, detail=f"malformed JSON-LD: {error}")

    if item_list is None:
        return HiristSearchClassification(
            state=HiristPageState.PARSE_FAILURE, detail="no schema.org ItemList JSON-LD block found"
        )

    raw_elements = item_list.get("itemListElement")
    if not isinstance(raw_elements, list):
        return HiristSearchClassification(
            state=HiristPageState.PARSE_FAILURE,
            detail="ItemList present but itemListElement is missing or not a list",
        )

    if len(raw_elements) == 0:
        return HiristSearchClassification(state=HiristPageState.VALID_EMPTY_RESULT, jobs=[], next_url=None)

    jobs = []
    for element in raw_elements:
        job = entry_parser(element)
        if job is not None:
            jobs.append(job)

    if not jobs:
        # Every element existed but none could be safely parsed -- NOT
        # the same as a genuine empty result (Hirist's own data claimed
        # results existed). Classified as a parse failure so this is
        # never silently mistaken for "no jobs found."
        return HiristSearchClassification(
            state=HiristPageState.PARSE_FAILURE,
            detail=f"itemListElement had {len(raw_elements)} entries but none could be safely parsed",
        )

    next_url = _find_next_page_url(html, current_url, allowed_host=allowed_next_host)

    return HiristSearchClassification(state=HiristPageState.VALID_RESULTS, jobs=jobs, next_url=next_url)
