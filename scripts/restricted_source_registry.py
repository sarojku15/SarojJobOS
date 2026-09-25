#!/usr/bin/env python3

"""
Registry, hostname validation, URL-pattern validation, and title
parsing for the seven job boards this project has no authorized direct
crawl access to (see data/reports/phase12_public_source_expansion.md
and phase13_broad_job_discovery.md for the per-source robots.txt/
anti-bot evidence). This module is pure and offline: no network call,
no fetch of any restricted board's page, ever. It only decides,
mechanically, whether a URL a search PROVIDER already returned (1)
genuinely belongs to the claimed board and (2) is a job-DETAIL page
rather than a search/listing/company/article page -- and, if so, parses
whatever title/company/location text the search result itself carried.

HARDENED HOSTNAME VALIDATION (Phase 14 explicit requirement): a naive
`"linkedin.com" in netloc` substring check would incorrectly accept a
lookalike host such as "notlinkedin.com.evil.example" (which contains
the substring "linkedin.com"). _hostname_matches() instead requires the
host to be EXACTLY the allowed domain or a proper dot-separated
subdomain of it.

Title parsing NEVER invents a missing field: when a pattern doesn't
confidently match, the corresponding field is left "" (UNKNOWN), never
guessed. Every parser here was designed against REAL search-provider
results captured in Phase 13 (see
scripts/web_search_evidence_capture.py) for six of these seven sources;
Shine returned zero real results in that phase, so its pattern below is
a best-effort, explicitly unvalidated-against-real-data fallback (noted
in its own comment).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse, urlunparse


# ---------------------------------------------------------------------
# Hardened hostname validation
# ---------------------------------------------------------------------

def _hostname_matches(netloc: str, allowed_domain: str) -> bool:
    """True only if netloc's HOST is exactly allowed_domain or a proper
    subdomain of it (e.g. "in.linkedin.com" matches "linkedin.com";
    "notlinkedin.com.evil.example" and "fake-linkedin.com" do not).
    Strips userinfo and port before comparing. Case-insensitive."""
    host = netloc.rsplit("@", 1)[-1].split(":", 1)[0].lower().rstrip(".")
    allowed = allowed_domain.lower()
    return host == allowed or host.endswith("." + allowed)


def is_safe_url(url: str, allowed_domain: str) -> bool:
    """Full URL safety check: http(s) scheme only, and an exactly-
    matching host per _hostname_matches()."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    return _hostname_matches(parsed.netloc, allowed_domain)


# ---------------------------------------------------------------------
# Title parsing -- one function per observed pattern family
# ---------------------------------------------------------------------

_SUFFIX = re.compile(
    r"\s*[|\-–•]\s*(LinkedIn|Indeed(?:\.com)?|foundit(?:\.in)?|Instahyre|"
    r"Cutshort|CutShort|Wellfound|Shine(?:\.com)?)\s*$",
    re.IGNORECASE,
)


def _strip_site_suffix(text: str) -> str:
    return _SUFFIX.sub("", text or "").strip()


def parse_linkedin_title(text: str) -> tuple[str, str, str]:
    """'<Company> hiring <Title> in <Location>...' -- LinkedIn's own
    search-result title shape, verified against 8 of 9 real results in
    Phase 13 (the 9th -- a Broadridge posting -- used a different
    phrasing with no "hiring" verb at all; Phase 13's one-off evidence
    script inferred its company from the URL slug, but this production
    parser deliberately does NOT do that: a URL-slug guess is close
    enough to "inventing" a field that this module treats it as
    unverified and honestly returns company="" for that case rather
    than risk a wrong company name)."""
    text = _strip_site_suffix(text)
    match = re.match(r"^(?P<company>.+?) hiring (?P<title>.+?) in (?P<location>.+)$", text, re.IGNORECASE)
    if match:
        return match.group("title").strip(), match.group("company").strip(), match.group("location").strip()
    return text, "", ""


def parse_dash_title(text: str) -> tuple[str, str, str]:
    """'Title - Company - Location' (3 dash-separated segments)."""
    text = _strip_site_suffix(text)
    parts = [p.strip() for p in re.split(r"\s+[-–|•]\s+", text) if p.strip()]
    if len(parts) >= 3:
        return parts[0], parts[1], ", ".join(parts[2:])
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return text, "", ""


def parse_indeed_title(text: str) -> tuple[str, str, str]:
    """Indeed's own search-result titles carry role + location only --
    NO company name (verified against all 9 real results captured in
    Phase 13; e.g. "Site Reliability Engineer - Bengaluru, Karnataka -
    Indeed.com"). Returning company="" here is the honest, evidence-
    based outcome, not a parsing failure -- do not "fix" this by
    reusing parse_dash_title(), which would silently misread the
    location segment as a company."""
    text = _strip_site_suffix(text)
    parts = [p.strip() for p in re.split(r"\s+-\s+", text) if p.strip()]
    if len(parts) >= 2:
        return parts[0], "", ", ".join(parts[1:])
    return text, "", ""


def parse_foundit_title(text: str) -> tuple[str, str, str]:
    """'<Title> at <Company> in <Location>' -- verified against 5/6
    real results in Phase 13 (the 6th used a different phrasing this
    parser correctly leaves as company="" rather than mis-parsing)."""
    text = _strip_site_suffix(text)
    match = re.match(r"^(?P<title>.+?) at (?P<company>.+?) in (?P<location>.+)$", text, re.IGNORECASE)
    if match:
        return match.group("title").strip(), match.group("company").strip(), match.group("location").strip()
    return text, "", ""


def parse_instahyre_title(text: str) -> tuple[str, str, str]:
    """'<Title> job at <Company> - Instahyre' -- verified against 9/9
    real results in Phase 13."""
    text = re.sub(r"\s*-\s*Instahyre\s*$", "", text or "", flags=re.IGNORECASE).strip()
    match = re.match(r"^(?P<title>.+?) job at (?P<company>.+)$", text, re.IGNORECASE)
    if match:
        return match.group("title").strip(), match.group("company").strip(), ""
    return text, "", ""


def parse_cutshort_title(text: str) -> tuple[str, str, str]:
    """Cutshort uses two different real phrasings (both verified in
    Phase 13): '<Company> is hiring <Title> job in <Location> |
    Cutshort' and '<Title> job in <Location> | <Company> is hiring on
    CutShort'."""
    text = _strip_site_suffix(text)
    match = re.match(r"^(?P<company>.+?) is hiring (?P<title>.+?) job in (?P<location>.+)$", text, re.IGNORECASE)
    if match:
        return match.group("title").strip(), match.group("company").strip(), match.group("location").strip()
    match = re.match(r"^(?P<title>.+?) job in (?P<location>.+?) \| (?P<company>.+?) is hiring on CutShort", text, re.IGNORECASE)
    if match:
        return match.group("title").strip(), match.group("company").strip(), match.group("location").strip()
    return text, "", ""


def parse_wellfound_title(text: str) -> tuple[str, str, str]:
    """'<Title> at <Company> • <Location>' -- verified against 3/3
    real results in Phase 13."""
    text = _strip_site_suffix(text)
    match = re.match(r"^(?P<title>.+?) at (?P<company>.+?)\s*•\s*(?P<location>.+)$", text)
    if match:
        return match.group("title").strip(), match.group("company").strip(), match.group("location").strip()
    match = re.match(r"^(?P<title>.+?) at (?P<company>.+)$", text)
    if match:
        return match.group("title").strip(), match.group("company").strip(), ""
    return text, "", ""


def parse_shine_title(text: str) -> tuple[str, str, str]:
    """UNVALIDATED against real data: Shine returned zero results for
    both queries tried in Phase 13 (see
    scripts/web_search_evidence_capture.py's module docstring), so this
    is a best-effort dash-parser (Shine's own job-detail URLs suggest a
    '/jobs/<title-slug>/<company-slug>/<id>' shape consistent with a
    'Title - Company - Location' title convention similar to Indeed/
    Foundit) -- treated with the SAME honesty rule as every other
    parser here (never invents a field), but its accuracy has not been
    confirmed against a single real Shine search result."""
    return parse_dash_title(text)


# ---------------------------------------------------------------------
# Site registry
# ---------------------------------------------------------------------

def _path_only_canonical(_job_id: str, raw_url: str) -> str:
    parsed = urlparse(raw_url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def _linkedin_canonical(job_id: str, _raw_url: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}/"


def _indeed_canonical(job_id: str, _raw_url: str) -> str:
    return f"https://in.indeed.com/viewjob?jk={job_id}"


@dataclass(frozen=True)
class RestrictedSite:
    key: str  # CommonJob "source" value, e.g. "LINKEDIN"
    domain: str  # exact/subdomain-matched host, e.g. "linkedin.com"
    site_filter: str  # used in the `site:` search operator
    detail_url_pattern: str  # must match a job-DETAIL url; group(1) = job id
    parse_title: Callable[[str], tuple[str, str, str]]
    canonical: Callable[[str, str], str] = _path_only_canonical


SITES: list[RestrictedSite] = [
    RestrictedSite(
        "LINKEDIN", "linkedin.com", "linkedin.com/jobs/view",
        r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d{8,})",
        parse_linkedin_title, _linkedin_canonical,
    ),
    RestrictedSite(
        "INDEED", "indeed.com", "in.indeed.com/viewjob",
        r"indeed\.com/.*[?&]jk=([0-9a-f]{16})",
        parse_indeed_title, _indeed_canonical,
    ),
    RestrictedSite(
        "FOUNDIT", "foundit.in", "foundit.in/job",
        r"foundit\.in/job/[^?#]*?-(\d{6,})",
        parse_foundit_title,
    ),
    RestrictedSite(
        "INSTAHYRE", "instahyre.com", "instahyre.com/job-",
        r"instahyre\.com/job-(\d+)-",
        parse_instahyre_title,
    ),
    RestrictedSite(
        "CUTSHORT", "cutshort.io", "cutshort.io/job",
        r"cutshort\.io/job/([^/?#]+)",
        parse_cutshort_title,
    ),
    RestrictedSite(
        "WELLFOUND", "wellfound.com", "wellfound.com/jobs",
        r"wellfound\.com/jobs/(\d+)-",
        parse_wellfound_title,
    ),
    RestrictedSite(
        "SHINE", "shine.com", "shine.com/jobs",
        r"shine\.com/jobs/[^?#]+/(\d{6,})",
        parse_shine_title,
    ),
]

SITE_BY_KEY = {s.key: s for s in SITES}
RESTRICTED_SOURCE_NAMES = frozenset(s.key for s in SITES)


def build_site_query(site: RestrictedSite, role: str, location: str) -> str:
    """One site-scoped query for ONE already-frozen JobOS SearchQuery
    (role, location) -- see search_provider_adapter.py, which calls
    this once per adapter.search(query) invocation, exactly mirroring
    how every other adapter in this project turns a SearchQuery into
    one request. Never reads a hardcoded role/location list -- works
    for any profession the candidate's own search profile specifies."""
    role_part = f'"{role}"' if role else ""
    location_part = location or ""
    return f"site:{site.site_filter} {role_part} {location_part}".strip()


@dataclass
class ValidatedHit:
    job_id: str
    canonical_url: str
    title: str
    company: str
    location: str
    description_snippet: str
    posted_hint: str | None
    completeness: float


def validate_and_parse_hit(site: RestrictedSite, hit: dict) -> tuple[ValidatedHit | None, str | None]:
    """Validate one raw search-provider hit against `site` and, if it
    passes, parse it. Returns (ValidatedHit, None) on success or
    (None, reject_reason) on failure. reject_reason is one of:
    "off_domain", "not_detail_page", "empty_url". Pure, no network,
    no fetch of the restricted site itself -- the hit's own title/
    snippet (already returned by the search provider) is the only data
    source."""
    url = (hit.get("url") or "").strip()
    if not url:
        return None, "empty_url"

    if not is_safe_url(url, site.domain):
        return None, "off_domain"

    match = re.search(site.detail_url_pattern, url)
    if not match:
        return None, "not_detail_page"

    job_id = match.group(1)
    title, company, location = site.parse_title(hit.get("title", ""))
    completeness = round(sum(bool(x) for x in (title, company, location)) / 3, 2)

    return (
        ValidatedHit(
            job_id=job_id,
            canonical_url=site.canonical(job_id, url),
            title=title,
            company=company,
            location=location,
            description_snippet=hit.get("snippet", "") or "",
            posted_hint=hit.get("date"),
            completeness=completeness,
        ),
        None,
    )
