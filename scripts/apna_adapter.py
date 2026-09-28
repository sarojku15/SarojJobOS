#!/usr/bin/env python3

"""
Apna adapter (Phase 12) -- real, working implementation.

Uses PLAIN, unauthenticated HTTP GET requests (no Playwright/browser
needed -- confirmed this phase that Apna's job-search page is
genuinely server-side rendered on the very first response; see
apna_parser.py's module docstring for the full evidence trail and
data/reports/phase12_public_source_expansion.md for the live
validation log).

Two-step, fully generic URL construction (never a hardcoded location
ID for any specific city):
  1. Resolve `query.location` (arbitrary text) to Apna's own
     location_identifier/location_type via its public, unauthenticated
     suggestions API (production.apna.co/suggester/.../location/suggestions)
     -- confirmed this phase to work for arbitrary location text, not
     just the one seed URL's Bangalore.
  2. Build https://apna.co/jobs?search=true&text=<role>&location_identifier=...
     using that resolved identifier.

robots.txt (checked fresh this phase and in Phase 11) is fully
permissive for a generic user agent on both apna.co and
production.apna.co -- no CAPTCHA/Turnstile/login-wall was observed on
either endpoint in any live check this phase.
"""

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request

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
from apna_parser import extract_job_feed, parse_job_feed_entries, find_next_page_url, parse_detail_page

_BASE_URL = "https://apna.co"
_JOBS_URL = f"{_BASE_URL}/jobs"
_LOCATION_SUGGESTIONS_URL = "https://production.apna.co/suggester/suggester/api/v1/location/suggestions"

_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
_FETCH_TIMEOUT_SECONDS = 15

_MAX_PAGES = 3

# Bounded detail-page enrichment (previously 0 -- "not implemented this
# phase"; a real, working implementation now exists, see
# apna_parser.parse_detail_page()). 20 is a conservative default,
# chosen the same way this project's other bounds are chosen: real
# listing pages return up to 75 raw jobs (3 pages x 25); fetching all
# of them sequentially at the existing 2s rate-limit courtesy pacing
# would take 2.5+ minutes for ONE query alone, disproportionate to the
# value (most of a search's raw results are never eligible/relevant
# anyway -- see job_eligibility.py). 20 enriches roughly the first
# quarter of a typical result page (Apna's own listing order, already
# its best-effort relevance ranking) in well under a minute, matching
# the pacing this project's other adapters already accept. Overridable
# per-instance via ApnaAdapter(max_detail_fetches_per_query=...), the
# same configuration pattern rate_limit_seconds already uses below --
# no separate config file/env var exists for per-adapter tuning
# elsewhere in this project, so none is introduced here either.
_MAX_DETAIL_FETCHES_PER_QUERY = 20


def _http_get(url):
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/json"})
    try:
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as error:
        raise AdapterTimeoutError("APNA", detail=f"GET {url} failed: {error}") from error
    # A timeout/reset occurring mid-body inside response.read() raises
    # a raw socket/http.client exception, not URLError -- confirmed
    # live in production ("[Errno 54] Connection reset by peer" /
    # "The read operation timed out"), each escaping uncaught and
    # aborting discover_from_sources()'s entire batch instead of being
    # isolated to this one query.
    except (TimeoutError, ConnectionError, http.client.HTTPException) as error:
        raise AdapterTimeoutError("APNA", detail=f"GET {url} failed: {error}") from error


def resolve_location(location_text):
    """
    Calls Apna's own public location-suggestions API for arbitrary
    location text and returns (identifier, type, name) for the
    best (first) suggestion, or (None, None, None) if nothing is
    found/resolvable. Never invents an identifier.
    """
    if not location_text or not location_text.strip():
        return None, None, None

    url = f"{_LOCATION_SUGGESTIONS_URL}?{urllib.parse.urlencode({'input': location_text, 'source': 'web'})}"
    try:
        status, body = _http_get(url)
    except AdapterTimeoutError:
        return None, None, None

    if status != 200:
        return None, None, None

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None, None, None

    suggestions = data.get("suggestions") or []
    if not suggestions:
        return None, None, None

    location = suggestions[0].get("location") or {}
    return location.get("identifier"), location.get("type"), location.get("name")


def _build_search_url(query: SearchQuery):
    override = (query.extra or {}).get("apna_search_url")
    if override:
        return override

    params = {"search": "true", "text": query.role}

    identifier, loc_type, loc_name = resolve_location(query.location)
    if identifier:
        params["location_identifier"] = identifier
        params["location_type"] = loc_type
        params["location_name"] = loc_name
    # No location resolved: search proceeds keyword-only, unfiltered by
    # location -- an honest best-effort, never a fabricated location match.

    return f"{_JOBS_URL}?{urllib.parse.urlencode(params)}"


class ApnaFetcher:
    """Plain HTTP fetcher -- no Playwright/browser needed for Apna
    (see module docstring). Mirrors HiristFetcher/IimjobsFetcher's
    single-method `fetch(url) -> str` contract so ApnaAdapter can be
    tested with the same injectable-fake-fetcher pattern."""

    def fetch(self, url: str) -> str:
        status, body = _http_get(url)
        if status != 200:
            raise AdapterBlockedError("APNA", BlockReason.UNKNOWN_BLOCK, detail=f"HTTP {status} for {url}")
        return body


class ApnaAdapter(JobSourceAdapter):
    """
    Phase 12: ENABLED. Real implementation, live-validated (see
    data/reports/phase12_public_source_expansion.md): 25 real jobs
    parsed correctly from a single page, pagination link discovered
    and followed, plain-HTTP-only (no browser needed).
    """

    name = "APNA"
    status = AdapterStatus.ENABLED
    capabilities = frozenset({AdapterCapability.SEARCH, AdapterCapability.PAGINATION})

    def __init__(self, fetcher=None, rate_limit_seconds=2, max_detail_fetches_per_query=_MAX_DETAIL_FETCHES_PER_QUERY):
        self._fetcher = fetcher if fetcher is not None else ApnaFetcher()
        # No robots.txt Crawl-delay was specified for apna.co (checked
        # fresh this phase) -- a small, conservative, self-imposed
        # pacing between successive page fetches is used anyway as a
        # good-faith courtesy, not a documented requirement.
        self._rate_limit_seconds = rate_limit_seconds
        self._max_detail_fetches_per_query = max_detail_fetches_per_query
        # Populated by the most recent search() call -- truthful counts
        # for the source-execution audit (search_run_sources.
        # details_json, via source_registry.py/search_worker.py), never
        # silently reported as full success when some detail fetches
        # failed. Read by source_registry.discover_from_sources() after
        # search() returns.
        self.last_search_details = {}

    def health_check(self) -> AdapterHealth:
        try:
            body = self._fetcher.fetch(_JOBS_URL)
        except (AdapterTimeoutError, AdapterBlockedError) as error:
            return AdapterHealth(source=self.name, reachable=False, block_reason=BlockReason.NETWORK_ERROR, detail=str(error))

        if "captcha" in body.lower() and "captchashown" not in body.lower():
            return AdapterHealth(source=self.name, reachable=False, block_reason=BlockReason.CAPTCHA, detail="captcha phrase detected")

        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query: SearchQuery) -> list:
        url = _build_search_url(query)

        visited_urls = set()
        all_jobs = []
        seen_urls = set()
        page_number = 0
        is_first_page = True

        while url and page_number < _MAX_PAGES:
            if url in visited_urls:
                break

            if page_number > 0 and self._rate_limit_seconds:
                time.sleep(self._rate_limit_seconds)

            visited_urls.add(url)
            page_number += 1

            try:
                html = self._fetcher.fetch(url)
            except AdapterTimeoutError:
                if is_first_page:
                    raise
                break
            except AdapterBlockedError:
                if is_first_page:
                    raise
                break

            job_feed = extract_job_feed(html)
            if job_feed is None:
                if is_first_page:
                    raise AdapterTimeoutError(self.name, detail="no initialSSRJobFeedData found in page -- structure may have changed")
                break

            for job in parse_job_feed_entries(job_feed):
                if job["job_url"] in seen_urls:
                    continue
                seen_urls.add(job["job_url"])
                all_jobs.append(job)

            url = find_next_page_url(html, url)
            is_first_page = False

        self.last_search_details = self._enrich_with_detail_pages(all_jobs)
        return all_jobs

    def _enrich_with_detail_pages(self, jobs):
        """
        Bounded, best-effort detail-page enrichment: fetches up to
        self._max_detail_fetches_per_query DISTINCT job_urls (already
        deduplicated by search()'s own seen_urls tracking above; this
        loop also dedupes independently as a second guard, never
        fetching the same URL twice even if that ever changed), parses
        each via apna_parser.parse_detail_page(), and MERGES the result
        into that job's own listing dict.

        Merge rule (never overwrite reliable listing data with empty/
        unknown detail data): a detail field is applied ONLY when the
        job's current value for that key is falsy (empty string/None)
        -- an already-present, non-empty listing value (e.g. a
        location or experience_required the listing itself provided)
        is never replaced by a detail-page value, even a real one; the
        two are expected to usually agree, and when they don't, the
        listing (which every job already has, and this project's
        existing tests already validate) stays authoritative.

        A single detail-page fetch/parse failure never aborts the rest
        of the batch or the query as a whole -- that job simply keeps
        its listing-only data, exactly like a job that was never
        selected for detail-fetching at all. Returns a small dict of
        truthful counts for the source-execution audit.
        """
        counts = {"detail_fetch_attempted": 0, "detail_fetch_succeeded": 0, "detail_fetch_failed": 0}

        if self._max_detail_fetches_per_query <= 0:
            return counts

        already_fetched_urls = set()

        for job in jobs:
            if counts["detail_fetch_attempted"] >= self._max_detail_fetches_per_query:
                break

            job_url = job.get("job_url")
            if not job_url or job_url in already_fetched_urls:
                continue
            already_fetched_urls.add(job_url)

            if counts["detail_fetch_attempted"] > 0 and self._rate_limit_seconds:
                time.sleep(self._rate_limit_seconds)

            counts["detail_fetch_attempted"] += 1

            try:
                detail_html = self._fetcher.fetch(job_url)
            except (AdapterTimeoutError, AdapterBlockedError):
                counts["detail_fetch_failed"] += 1
                continue

            enrichment = parse_detail_page(detail_html, job_url)
            if not enrichment:
                counts["detail_fetch_failed"] += 1
                continue

            for key, value in enrichment.items():
                if not job.get(key):
                    job[key] = value

            counts["detail_fetch_succeeded"] += 1

        return counts
