#!/usr/bin/env python3

import re
import sys
import time

from source_adapter import (
    JobSourceAdapter,
    SearchQuery,
    AdapterHealth,
    BlockReason,
    AdapterBlockedError,
    AdapterTimeoutError,
    AdapterCapability,
)
from naukri_fetcher import NaukriFetcher
from naukri_parser import (
    detect_block_reason,
    parse_detail_page,
    classify_search_page,
    SearchPageState,
)


_HOMEPAGE_URL = "https://www.naukri.com/"


def _slugify(value):
    """
    Deterministic, conservative slugification: lowercase, collapse any
    run of non-alphanumeric characters into a single hyphen, trim
    leading/trailing hyphens.

    Verified live against exactly one real combo this session
    (role="Senior Site Reliability Engineer", location="Bengaluru" ->
    "senior-site-reliability-engineer" / "bengaluru"). Other
    role/location combos from config/searches.json are unverified --
    see query.extra["naukri_search_url"] as the escape hatch.
    """
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


# Naukri's own native "freshness" search-page filter, confirmed present
# in the real captured fixture (data/fixtures/naukri/search_results_sre_bengaluru.html):
# a single-select dropdown with exactly these five discrete day values
# (data-id="filter-freshness-{N}", e.g. title="Last 3 days" for N=3).
# There is no continuous/arbitrary-day option -- only these five.
_NAUKRI_FRESHNESS_OPTIONS = (1, 3, 7, 15, 30)

# The confirmed fixture only shows the filter WIDGET, not a captured
# example of the resulting filtered URL's query string -- so the actual
# parameter NAME below ("jobAge") is a best-effort, UNVERIFIED mapping,
# not something this project has proven against a live filtered
# request yet. This is the same kind of documented, honest gap
# _slugify() already carries for role/location combos beyond the one
# verified live -- to be confirmed by the next live validation. If it
# turns out wrong, Naukri is expected to simply ignore the unrecognized
# parameter and return its normal, unfiltered results (which
# classify_search_page() will still classify correctly either way);
# it is not expected to cause a block or a parse failure.
_NAUKRI_FRESHNESS_PARAM = "jobAge"


def _map_to_naukri_freshness_option(requested_days):
    """
    Map an arbitrary requested max_job_age_days to the closest native
    Naukri freshness option WITHOUT EVER EXCEEDING the request -- e.g.
    requested=3 -> 3 (exact match); requested=2 -> 1 (the largest
    native option that still satisfies "no older than 2 days"), never
    7 (which would violate the candidate's stated constraint). If the
    request is stricter than Naukri's strictest native option (1 day),
    the strictest available option is used rather than refusing
    outright -- Naukri simply cannot filter more tightly than that.

    Returns None (no filter applied) when requested_days is None
    (unrestricted -- the pre-existing, backward-compatible behavior).
    """
    if requested_days is None:
        return None

    not_exceeding = [opt for opt in _NAUKRI_FRESHNESS_OPTIONS if opt <= requested_days]
    if not_exceeding:
        return max(not_exceeding)

    return min(_NAUKRI_FRESHNESS_OPTIONS)


def _build_search_url(query: SearchQuery) -> str:
    """
    query.extra["naukri_search_url"], if present, is used verbatim --
    an escape hatch for a role/location combo whose guessed slug turns
    out wrong. Otherwise builds the one URL pattern proven live this
    session, plus Naukri's native freshness-filter query parameter when
    query.max_job_age_days is set (see _map_to_naukri_freshness_option()
    and _NAUKRI_FRESHNESS_PARAM's docstring for the honest caveat on
    the parameter name). Page 1 only: no page-2 pattern exists or is
    guessed.
    """
    override = (query.extra or {}).get("naukri_search_url")
    if override:
        return override

    role_slug = _slugify(query.role)
    location_slug = _slugify(query.location)
    url = f"https://www.naukri.com/{role_slug}-jobs-in-{location_slug}"

    freshness_option = _map_to_naukri_freshness_option(
        getattr(query, "max_job_age_days", None)
    )
    if freshness_option is not None:
        url += f"?{_NAUKRI_FRESHNESS_PARAM}={freshness_option}"

    return url


class NaukriAdapter(JobSourceAdapter):
    """
    Naukri source adapter. Page 1 only in this version -- no
    pagination is implemented or guessed at.

    Fetching is delegated entirely to an injectable fetcher
    (NaukriFetcher by default, which shells out to the Node/Playwright
    bridge). Tests inject a FakeFetcher instead, so no network, Node,
    or Playwright process is ever invoked during testing.

    Failure handling is deliberately not all-or-nothing:
    - a blocked search page raises AdapterBlockedError with zero jobs
      (nothing useful was ever obtained);
    - a blocked detail page stops further detail fetching but returns
      the jobs already successfully parsed, as a normal return rather
      than an exception;
    - a detail-page timeout skips just that one candidate and
      continues with the rest, with no retry.

    health_check_is_advisory = True: health_check() and search() request
    different URLs (the bare homepage vs. an actual role/location search
    page) through the identical fetch mechanism, with no shared browser
    session between them. Live evidence gathered across this project's
    validation runs never once demonstrated the homepage health check
    failing while a search was ALSO attempted in the same run to see
    whether it would have succeeded -- every prior successful run had
    both succeed together, and every recent failure was a homepage-only
    block that pre-emptively skipped every query before search() ever
    ran (see data/reports/naukri_health_check_architecture.md for the
    full evidence trail). A homepage-only failure is therefore not
    established as predictive of search-page availability for this
    source. search() already has its own complete, authoritative
    availability signal via classify_search_page() (VALID_RESULTS /
    VALID_EMPTY_RESULT / BLOCKED / SOFT_BLOCK_OR_CHALLENGE /
    PARSE_FAILURE) and its own AdapterBlockedError/AdapterTimeoutError
    raises -- unchanged by this flag. health_check() itself is
    unchanged and still runs, still recorded for diagnostics; it simply
    no longer pre-emptively prevents a query from being attempted.
    """

    name = "NAUKRI"
    health_check_is_advisory = True

    # Purely declarative -- added in the Phase 4 multi-source adapter
    # architecture work, changes no behavior in this file. Reflects what
    # this adapter's search()/detail-fetch logic (above) actually does
    # today: PAGINATION is deliberately absent ("Page 1 only" -- see
    # class docstring), as are COMPANY_METADATA/SALARY/REMOTE_FILTER/
    # LOCATION_FILTER, none of which parse_detail_page()/search() extract.
    capabilities = frozenset({
        AdapterCapability.SEARCH,
        AdapterCapability.DETAIL,
        AdapterCapability.NATIVE_FRESHNESS,
        AdapterCapability.APPLICATION_URL,
    })

    def __init__(self, fetcher=None, rate_limit_seconds=3):
        self._fetcher = fetcher if fetcher is not None else NaukriFetcher()
        self._rate_limit_seconds = rate_limit_seconds

    def health_check(self) -> AdapterHealth:
        try:
            html = self._fetcher.fetch(_HOMEPAGE_URL)
        except AdapterTimeoutError:
            # A health-check timeout is reported as an unhealthy
            # result, not raised -- the registry's health-check call
            # site does not expect health_check() to raise.
            return AdapterHealth(
                source=self.name,
                reachable=False,
                block_reason=BlockReason.TIMEOUT,
                detail="health check fetch timed out",
            )

        reason = detect_block_reason(html)

        return AdapterHealth(
            source=self.name,
            reachable=(reason == BlockReason.NONE),
            block_reason=reason,
        )

    def search(self, query: SearchQuery) -> list:
        url = _build_search_url(query)

        # A timeout fetching the search page itself is not caught
        # here: it propagates as AdapterTimeoutError, which the
        # registry already treats as a query-level failure.
        search_html = self._fetcher.fetch(url)

        classification = classify_search_page(search_html)

        if classification.state == SearchPageState.BLOCKED:
            raise AdapterBlockedError(
                self.name, classification.block_reason, detail=classification.detail
            )

        if classification.state == SearchPageState.SOFT_BLOCK_OR_CHALLENGE:
            # Reachable, but not a recognized search-results shell and
            # showing challenge/interstitial evidence. Treated as an
            # explicit block -- never silently reinterpreted as an
            # empty result -- see classify_search_page()'s safety rule.
            raise AdapterBlockedError(
                self.name,
                BlockReason.UNKNOWN_BLOCK,
                detail=f"SOFT_BLOCK_OR_CHALLENGE: {classification.detail}",
            )

        if classification.state == SearchPageState.PARSE_FAILURE:
            # Unrecognized page structure with zero job links. Raised
            # as a query-level failure (the existing AdapterTimeoutError
            # bucket -- skips just this query, does not block the whole
            # source, and is never counted as a succeeded query) rather
            # than returned as [], so this can never be mistaken for a
            # legitimate empty result downstream.
            raise AdapterTimeoutError(
                self.name, detail=f"PARSE_FAILURE: {classification.detail}"
            )

        # VALID_RESULTS or VALID_EMPTY_RESULT: a positively classified
        # search-results page. Zero links here means the search
        # genuinely found nothing, not that classification was skipped.
        candidates = classification.job_links

        # Deduplicate candidate job URLs within this search call,
        # first-seen order preserved. parse_search_results() already
        # dedupes within one page, but this makes the guarantee
        # explicit at the adapter level and holds even once a future
        # version fetches more than one page.
        seen_urls = set()
        unique_candidates = []
        for candidate in candidates:
            job_url = candidate.get("job_url")
            if job_url in seen_urls:
                continue
            seen_urls.add(job_url)
            unique_candidates.append(candidate)

        raw_jobs = []

        for candidate in unique_candidates:
            if self._rate_limit_seconds:
                time.sleep(self._rate_limit_seconds)

            job_url = candidate["job_url"]

            try:
                detail_html = self._fetcher.fetch(job_url)
            except AdapterTimeoutError:
                # Skip just this one candidate, no retry, keep going.
                continue

            reason = detect_block_reason(detail_html)

            if reason != BlockReason.NONE:
                # Distinguish this in any real run's output from an
                # ordinary successful search, without changing the
                # registry contract: this is still a normal return,
                # not an exception -- the registry sees a successful
                # query with fewer jobs than the search page promised.
                print(
                    f"{self.name}: search completed with partial "
                    f"results ({len(raw_jobs)} job(s)) because a "
                    f"detail page was blocked ({reason.value}) at "
                    f"{job_url}",
                    file=sys.stderr,
                )
                break

            raw_jobs.append(parse_detail_page(detail_html, job_url))

        return raw_jobs
