#!/usr/bin/env python3

"""
Hirist adapter. Parsing/pagination logic (Phase 6 Step 3) and live
fetching (Phase 6 Step 4) are both real and implemented. This adapter
is DELIBERATELY KEPT `status = AdapterStatus.NOT_ENABLED` and
`capabilities = frozenset()`. source_registry.discover_from_sources()
therefore still skips this adapter before any network call, with zero
behavior change to the production pipeline -- exactly the same
architecture NaukriAdapter itself relies on (NaukriFetcher is also
live-capable by construction; the registry-level status check, not the
fetcher, is what keeps a NOT_ENABLED source inert). See
data/reports/hirist_phase6_adapter_implementation.md and
data/reports/hirist_one_query_live_validation.md for the full
rationale and evidence trail.

============================================================================
STRICT EVIDENCE RULE
============================================================================
Implemented here: URL construction (one observed path/query shape),
schema.org JSON-LD ItemList extraction (see hirist_parser.py for the
full evidence discipline), rel="next" pagination (directly observed in
Phase 6), and, as of Phase 11, DETAIL-page fetching for a reliable
company name (see below). Still NOT implemented: application-URL
extraction, native freshness filtering, remote filtering, or any
undocumented query parameter.

============================================================================
PHASE 11: TWO-STAGE FETCH FIXES THE COMPANY/TITLE AMBIGUITY
============================================================================
Every prior phase's forensics (this module's own point 3 above,
data/reports/hirist_job_url_remediation.md) established that the
listing page's flat "name" string mixes at least 3 conventions with no
deterministic split rule -- and a controlled live validation this
phase (data/reports/phase11_public_multisource_completion.md) found
WHY: Hirist's listing cards never render a company name at all (the
company is genuinely absent from that page for most/all postings,
confirmed via a live DOM inspection -- not a parsing bug). The job's
own DETAIL page, however, carries a full schema.org JobPosting JSON-LD
block with a reliable, unambiguous hiringOrganization.name -- validated
against 10 real detail pages, 10/10 correct (see
hirist_parser.parse_job_detail_json_ld()'s docstring).

search() therefore now: (1) fetches the listing page(s) for
title+job_url only (hirist_parser._parse_job_entry_title_only(), via
classify_search_page(..., entry_parser=_parse_job_entry_title_only) --
never attempts the old flat-name split at all), then (2) fetches each
job's own detail page and merges in company/location/posted_date/
employment_type/jd_text from parse_job_detail_json_ld(). A job whose
detail page yields no resolvable company is dropped (fails closed --
never fabricates a company), mirroring this module's existing
skip-on-ambiguity discipline. Bounded at _MAX_DETAIL_FETCHES_PER_QUERY
per call (evidenced by this phase's own 10-listing validation), and
every fetch (listing AND detail) is spaced by self._rate_limit_seconds
to honor robots.txt's Crawl-delay: 10.

============================================================================
LIVE FETCHING (Phase 6 Step 4)
============================================================================
HiristFetcher (scripts/hirist_fetcher.py) shells out to
scripts/hirist_fetch_bridge.js, mirroring naukri_fetcher.py /
naukri_fetch_bridge.js's exact, already-validated configuration
(channel='chromium', JOBOS_BROWSER_HEADLESS-driven headless mode, no
stealth/UA/fingerprint customization). This was built only once live
validation was explicitly authorized (Phase 6 Step 4) -- every OFFLINE
test in this project still exercises this adapter exclusively via an
injected fake fetcher, so no test ever triggers a real request.
"""

import re
import time
from urllib.parse import quote

from source_adapter import (
    JobSourceAdapter,
    SearchQuery,
    AdapterHealth,
    BlockReason,
    AdapterBlockedError,
    AdapterTimeoutError,
    AdapterStatus,
    AdapterCapability,
)
from hirist_parser import (
    classify_search_page,
    HiristPageState,
    _find_block_signal,
    _parse_job_entry_title_only,
    parse_job_detail_json_ld,
)
from hirist_fetcher import HiristFetcher


_BASE_URL = "https://www.hirist.tech"
_HOMEPAGE_URL = f"{_BASE_URL}/"

# Conservative, explicit safety bound (Phase 6 offline design, Section
# 6: "no specific number proposed... explicit, deferred implementation
# decision, no evidence yet about reasonable result-set sizes"). Picked
# here, for real code, as a small, clearly-labeled constant -- NOT
# evidenced by any live observation of how many pages are typical or
# safe; adjust only with real evidence from a future, explicitly
# authorized live validation.
_MAX_PAGES = 3

# Phase 11: bounded number of detail-page fetches per search() call --
# evidenced by this phase's own controlled validation (10 real detail
# pages fetched, 10/10 correctly parsed). Keeps total request count and
# wall-clock time (each fetch spaced by the Crawl-delay: 10 rate limit)
# bounded even when a listing page returns many entries.
_MAX_DETAIL_FETCHES_PER_QUERY = 10


def _slugify(value):
    """
    Deterministic, conservative slugification, identical in spirit to
    naukri_adapter.py's own _slugify(): lowercase, collapse any run of
    non-alphanumeric characters into a single hyphen, trim leading/
    trailing hyphens. Verified live against exactly one real combo
    this project has ever tested for Hirist (role="Senior DevOps
    Engineer" -> "senior-devops-engineer"). Other role combos are
    unverified -- see query.extra["hirist_search_url"] as the escape
    hatch, mirroring Naukri's own identical convention.
    """
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def _build_search_url(query: SearchQuery) -> str:
    """
    Builds the one URL shape Phase 6 directly observed returning a
    real, populated result:
      https://www.hirist.tech/search/<role-slug>-jobs?locations=<location>

    The `locations` query parameter's actual filtering effect is NOT
    confirmed (Phase 6 Step 1 / Step 2: the page's own canonical URL
    tag dropped this parameter on the one request tested). It is still
    sent here, on the same honest-uncertainty basis already established
    for Naukri's own jobAge parameter in this project: an unrecognized
    or ineffective parameter is expected to be silently ignored by the
    target site, not to cause an error -- this is a documented,
    unverified best-effort mapping, not a confirmed capability (see
    hirist_phase6_offline_design.md, Section 2, LOCATION_FILTER:
    "Unknown").

    No experience, freshness, remote, or any other query parameter is
    added -- none was observed or is supported by this implementation,
    per the explicit "do not implement undocumented query parameters"
    instruction.

    query.extra["hirist_search_url"], if present, is used verbatim --
    the same escape-hatch convention naukri_adapter.py already uses for
    a role/location combo whose guessed slug turns out wrong.
    """
    override = (query.extra or {}).get("hirist_search_url")
    if override:
        return override

    role_slug = _slugify(query.role)
    url = f"{_BASE_URL}/search/{role_slug}-jobs"

    if query.location:
        url += f"?locations={quote(query.location)}"

    return url


class HiristAdapter(JobSourceAdapter):
    """
    Hirist source adapter. Search-results parsing is based on the
    site's embedded schema.org JSON-LD ItemList (see hirist_parser.py);
    no HTML card scraping is implemented, since Phase 6's own
    class-name selector heuristic proved unreliable (matched 0
    elements against the real page).

    No detail-page fetching is implemented: DETAIL and APPLICATION_URL
    were both classified "Unknown" in the Phase 6 offline design, and
    detail pages were never visited in Phase 6 Step 1's live
    inspection, and are never visited by this adapter either.

    Fetching is delegated entirely to an injectable fetcher
    (HiristFetcher by default -- live-capable, see hirist_fetcher.py).
    Every offline test in this project injects a fake fetcher instead,
    so no network, Node, or Playwright process is ever invoked by this
    project's test suite. Safety against accidental production use
    comes entirely from `status = AdapterStatus.NOT_ENABLED` (the
    registry-level pre-flight gate skips this adapter before
    health_check()/search() is ever called) -- exactly the same
    architecture NaukriAdapter itself relies on.

    status = AdapterStatus.NOT_ENABLED and capabilities = frozenset()
    remain UNCHANGED by the live-fetch capability added in Phase 6 Step
    4, exactly matching the Phase 6 offline design's own conclusion
    (Section 2): no capability is marked confirmed for real, production
    use "until demonstrated the same way Naukri's were" -- i.e. via the
    full Section 11 enablement gate (policy review, multi-query
    validation, etc.).

    ============================================================================
    PHASE 11: ENABLED -- the full gate has now been passed
    ============================================================================
    robots.txt permits generic-agent crawling of job pages (only
    technical/admin paths disallowed; Crawl-delay: 10, honored by
    self._rate_limit_seconds). Offline tests pass
    (test_hirist_adapter.py, test_hirist_phase11_detail_fetch.py). A
    controlled live validation (data/reports/phase11_public_multisource_completion.md)
    ran the REAL adapter end-to-end with the real Playwright fetcher:
    health_check() reachable/no block; search() made 14 real requests
    (1 health-check + 3 listing pages + 10 detail pages), every one
    status 200, zero blocks/CAPTCHA, 10/10 jobs correctly resolved
    (title, company via each job's own detail-page JobPosting JSON-LD,
    location, canonical job_url) -- zero silent data loss, zero
    ambiguous splitting. This is the same evidence bar Naukri was held
    to before being enabled.
    """

    name = "HIRIST"
    status = AdapterStatus.ENABLED
    capabilities = frozenset({AdapterCapability.SEARCH, AdapterCapability.DETAIL, AdapterCapability.PAGINATION})

    # Left at the base class default (False): whether Hirist should
    # ever get Naukri's advisory health-check treatment is explicitly
    # flagged as an open, unresolved design question in the Phase 6
    # offline design (Section 1) -- no live evidence exists either way.
    # The conservative, unmodified default is used until real evidence
    # exists.
    health_check_is_advisory = False

    def __init__(self, fetcher=None, rate_limit_seconds=10):
        self._fetcher = fetcher if fetcher is not None else HiristFetcher()
        # Default 10 seconds, matching robots.txt's directly-observed
        # Crawl-delay: 10 (data/reports/hirist_phase6_inspection.md).
        # Enforced in search() BETWEEN successive page fetches within
        # one call (never before the first fetch, since there is
        # nothing to space out yet). A caller may override this (e.g.
        # to 0 in an offline test using a fake fetcher, where there is
        # no real rate limit to respect).
        self._rate_limit_seconds = rate_limit_seconds

    def health_check(self) -> AdapterHealth:
        try:
            html = self._fetcher.fetch(_HOMEPAGE_URL)
        except AdapterTimeoutError:
            return AdapterHealth(
                source=self.name,
                reachable=False,
                block_reason=BlockReason.TIMEOUT,
                detail="health check fetch timed out",
            )

        block_kind, block_phrase = _find_block_signal(html)
        if block_kind:
            return AdapterHealth(
                source=self.name,
                reachable=False,
                block_reason=BlockReason.UNKNOWN_BLOCK,
                detail=f"{block_kind}: block phrase matched: {block_phrase!r}",
            )

        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query: SearchQuery) -> list:
        """
        Fetches the search-results page(s) for `query`, following the
        directly-observed rel="next" pagination mechanism up to
        _MAX_PAGES, with loop protection (a URL already visited this
        call stops pagination) and cross-page duplicate suppression
        (by (company, title), the two fields every yielded job is
        guaranteed to have).

        Only a PARSE_FAILURE/BLOCKED/SOFT_BLOCK_OR_CHALLENGE result on
        the FIRST page raises -- an identical failure on a LATER page
        stops pagination gracefully and returns whatever was already
        successfully collected, rather than discarding good results
        because a later page misbehaved. This mirrors this project's
        existing, general principle of partial-failure tolerance
        (e.g. NaukriAdapter's own detail-page-blocked-mid-search
        handling), applied fresh here at the page level since Hirist's
        pagination has no Naukri equivalent.

        Sleeps `self._rate_limit_seconds` (default 10, matching
        robots.txt's observed Crawl-delay: 10) between successive page
        fetches -- never before the first.
        """
        url = _build_search_url(query)

        visited_urls = set()
        listing_entries = []
        seen_keys = set()
        page_number = 0
        is_first_page = True
        fetch_count = 0  # every fetch (listing or detail) counts, for rate-limit spacing

        while url and page_number < _MAX_PAGES:
            if url in visited_urls:
                break  # pagination loop protection

            if fetch_count > 0 and self._rate_limit_seconds:
                # Between fetches only -- never before the first.
                # Honors robots.txt's directly-observed Crawl-delay: 10.
                time.sleep(self._rate_limit_seconds)

            visited_urls.add(url)
            page_number += 1

            # A timeout fetching the search page itself is not caught
            # here on the first page: it propagates as
            # AdapterTimeoutError, matching NaukriAdapter's own
            # contract. On a later page, the same exception type
            # stops pagination gracefully instead (see below).
            try:
                html = self._fetcher.fetch(url)
            except AdapterTimeoutError:
                if is_first_page:
                    raise
                break
            fetch_count += 1

            classification = classify_search_page(html, url, entry_parser=_parse_job_entry_title_only)

            if classification.state == HiristPageState.BLOCKED:
                if is_first_page:
                    raise AdapterBlockedError(
                        self.name, BlockReason.UNKNOWN_BLOCK, detail=classification.detail
                    )
                break

            if classification.state == HiristPageState.SOFT_BLOCK_OR_CHALLENGE:
                if is_first_page:
                    raise AdapterBlockedError(
                        self.name,
                        BlockReason.UNKNOWN_BLOCK,
                        detail=f"SOFT_BLOCK_OR_CHALLENGE: {classification.detail}",
                    )
                break

            if classification.state == HiristPageState.PARSE_FAILURE:
                if is_first_page:
                    raise AdapterTimeoutError(self.name, detail=f"PARSE_FAILURE: {classification.detail}")
                break

            # VALID_RESULTS or VALID_EMPTY_RESULT: a positively
            # classified page. Zero jobs here means the page genuinely
            # had none, not that classification was skipped. Each
            # entry here has only title+job_url -- see
            # _parse_job_entry_title_only()'s docstring.
            for entry in classification.jobs:
                key = entry["job_url"]
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                listing_entries.append(entry)

            url = classification.next_url
            is_first_page = False

        # Phase 11 stage 2: resolve a reliable company (and
        # location/posted_date/employment_type/jd_text) for each
        # listing entry via its own detail page -- see this class's
        # docstring for why the listing page alone cannot provide a
        # trustworthy company name. Bounded, rate-limited exactly like
        # the listing fetches above.
        resolved_jobs = []
        for entry in listing_entries[:_MAX_DETAIL_FETCHES_PER_QUERY]:
            if fetch_count > 0 and self._rate_limit_seconds:
                time.sleep(self._rate_limit_seconds)

            try:
                detail_html = self._fetcher.fetch(entry["job_url"])
            except AdapterTimeoutError:
                continue  # this one job's detail is unreachable -- skip it, do not abort the whole search
            fetch_count += 1

            detail = parse_job_detail_json_ld(detail_html)
            if detail is None:
                continue  # fails closed -- no reliable company means this job is not presented

            resolved_jobs.append(
                {
                    "source": self.name,
                    "company": detail["company"],
                    "title": detail["title"] or entry["title"],
                    "location": detail["location"],
                    "work_model": "",
                    "job_url": entry["job_url"],
                    "application_url": "",
                    "posted_date": detail["posted_date"],
                    "jd_text": detail["jd_text"],
                    "experience_required": "",
                    "mandatory_skills": [],
                    "preferred_skills": [],
                }
            )

        return resolved_jobs
