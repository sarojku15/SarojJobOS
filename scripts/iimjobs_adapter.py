#!/usr/bin/env python3

"""
iimjobs adapter (Phase 11). Real, working implementation -- see
iimjobs_parser.py's module docstring for why this reuses
hirist_parser.py's generic schema.org parsing logic almost entirely
(iimjobs shares Hirist's exact underlying platform).

Same two-stage pipeline as HiristAdapter.search(): fetch listing
page(s) for title+job_url only, then fetch each job's own detail page
for a reliable company name via schema.org JobPosting.hiringOrganization.name
-- never the ambiguous flat "name" string split. See
data/reports/phase11_public_multisource_completion.md for the
controlled live validation that grounds this (offline design ->
one live listing fetch -> one live detail fetch -> 10-listing detail
validation -> full end-to-end live validation, mirroring exactly the
same gate Hirist was held to).

Only the `location` query parameter's filtering effect is unconfirmed
(same honest-uncertainty posture as Hirist's own "locations" param --
see _build_search_url()) -- combined into the free-text `q` search
term as a best-effort mapping, never a confirmed capability.
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
from iimjobs_parser import IimjobsPageState, classify_search_page, parse_job_detail_json_ld, _find_block_signal
from iimjobs_fetcher import IimjobsFetcher


_BASE_URL = "https://www.iimjobs.com"
_HOMEPAGE_URL = f"{_BASE_URL}/"

_MAX_PAGES = 3
_MAX_DETAIL_FETCHES_PER_QUERY = 10


def _build_search_url(query: SearchQuery) -> str:
    """
    https://www.iimjobs.com/search/j?q=<role>[ <location>]

    query.extra["iimjobs_search_url"], if present, is used verbatim --
    the same escape-hatch convention naukri_adapter.py/hirist_adapter.py
    already use.
    """
    override = (query.extra or {}).get("iimjobs_search_url")
    if override:
        return override

    search_text = query.role
    if query.location:
        search_text = f"{query.role} {query.location}"

    return f"{_BASE_URL}/search/j?q={quote(search_text)}"


class IimjobsAdapter(JobSourceAdapter):
    """
    Phase 11: ENABLED. Passed the same live-validation gate Hirist did
    -- see this module's docstring and
    data/reports/phase11_public_multisource_completion.md.
    """

    name = "IIMJOBS"
    status = AdapterStatus.ENABLED
    capabilities = frozenset({AdapterCapability.SEARCH, AdapterCapability.DETAIL, AdapterCapability.PAGINATION})

    health_check_is_advisory = False

    def __init__(self, fetcher=None, rate_limit_seconds=10):
        self._fetcher = fetcher if fetcher is not None else IimjobsFetcher()
        # Matches robots.txt's observed Crawl-delay: 10 (identical to
        # Hirist's own).
        self._rate_limit_seconds = rate_limit_seconds

    def health_check(self) -> AdapterHealth:
        try:
            html = self._fetcher.fetch(_HOMEPAGE_URL)
        except AdapterTimeoutError:
            return AdapterHealth(
                source=self.name, reachable=False, block_reason=BlockReason.TIMEOUT,
                detail="health check fetch timed out",
            )

        block_kind, block_phrase = _find_block_signal(html)
        if block_kind:
            return AdapterHealth(
                source=self.name, reachable=False, block_reason=BlockReason.UNKNOWN_BLOCK,
                detail=f"{block_kind}: block phrase matched: {block_phrase!r}",
            )

        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query: SearchQuery) -> list:
        url = _build_search_url(query)

        visited_urls = set()
        listing_entries = []
        seen_keys = set()
        page_number = 0
        is_first_page = True
        fetch_count = 0

        while url and page_number < _MAX_PAGES:
            if url in visited_urls:
                break

            if fetch_count > 0 and self._rate_limit_seconds:
                time.sleep(self._rate_limit_seconds)

            visited_urls.add(url)
            page_number += 1

            try:
                html = self._fetcher.fetch(url)
            except AdapterTimeoutError:
                if is_first_page:
                    raise
                break
            fetch_count += 1

            classification = classify_search_page(html, url)

            if classification.state == IimjobsPageState.BLOCKED:
                if is_first_page:
                    raise AdapterBlockedError(self.name, BlockReason.UNKNOWN_BLOCK, detail=classification.detail)
                break

            if classification.state == IimjobsPageState.SOFT_BLOCK_OR_CHALLENGE:
                if is_first_page:
                    raise AdapterBlockedError(
                        self.name, BlockReason.UNKNOWN_BLOCK, detail=f"SOFT_BLOCK_OR_CHALLENGE: {classification.detail}"
                    )
                break

            if classification.state == IimjobsPageState.PARSE_FAILURE:
                if is_first_page:
                    raise AdapterTimeoutError(self.name, detail=f"PARSE_FAILURE: {classification.detail}")
                break

            for entry in classification.jobs:
                key = entry["job_url"]
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                listing_entries.append(entry)

            url = classification.next_url
            is_first_page = False

        resolved_jobs = []
        for entry in listing_entries[:_MAX_DETAIL_FETCHES_PER_QUERY]:
            if fetch_count > 0 and self._rate_limit_seconds:
                time.sleep(self._rate_limit_seconds)

            try:
                detail_html = self._fetcher.fetch(entry["job_url"])
            except AdapterTimeoutError:
                continue
            fetch_count += 1

            detail = parse_job_detail_json_ld(detail_html)
            if detail is None:
                continue

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
