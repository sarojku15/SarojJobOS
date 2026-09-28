#!/usr/bin/env python3

from datetime import datetime, timezone

from source_adapter import (
    MockJobSourceAdapter,
    SearchQuery,
    BlockReason,
    AdapterBlockedError,
    AdapterTimeoutError,
    SourceRunState,
    AdapterStatus,
)
from naukri_adapter import NaukriAdapter
from linkedin_adapter import LinkedInAdapter
from indeed_adapter import IndeedAdapter
from foundit_adapter import FounditAdapter
from instahyre_adapter import InstahyreAdapter
from wellfound_adapter import WellfoundAdapter
from career_page_adapter import CareerPageAdapter
from hirist_adapter import HiristAdapter
from cutshort_adapter import CutshortAdapter
from shine_adapter import ShineAdapter
from timesjobs_adapter import TimesJobsAdapter
from iimjobs_adapter import IimjobsAdapter
from greenhouse_adapter import GreenhouseAdapter
from lever_adapter import LeverAdapter
from ashby_adapter import AshbyAdapter
from web_search_discovery_adapter import WebSearchDiscoveryAdapter
from apna_adapter import ApnaAdapter
from search_provider_adapter import SEARCH_PROVIDER_ADAPTERS


ADAPTERS = {
    "MOCK": MockJobSourceAdapter,
    "NAUKRI": NaukriAdapter,
    # Phase 4 skeletons -- registered so the pipeline can reason about
    # these sources structurally, but every one is AdapterStatus.
    # NOT_ENABLED (see each module's own docstring): discover_from_
    # sources() below skips them before any network call, and their
    # health_check()/search() raise AdapterNotEnabledError if ever
    # invoked directly. None of these has been live-validated -- see
    # data/reports/multi_source_adapter_architecture.md. This is the
    # complete, agreed target source list; Glassdoor is explicitly NOT
    # part of it (removed after being added to documentation by mistake
    # -- see that report's changelog).
    "LINKEDIN": LinkedInAdapter,
    "HIRIST": HiristAdapter,
    "INDEED": IndeedAdapter,
    "FOUNDIT": FounditAdapter,
    "INSTAHYRE": InstahyreAdapter,
    "CUTSHORT": CutshortAdapter,
    "WELLFOUND": WellfoundAdapter,
    "SHINE": ShineAdapter,
    "TIMESJOBS": TimesJobsAdapter,
    "IIMJOBS": IimjobsAdapter,
    "CAREER_PAGE": CareerPageAdapter,
    # Phase 10 (job-discovery-engine) additions -- generic ATS/career-page
    # providers (real implementations against each platform's public,
    # documented JSON API -- see each module's docstring) and the Web
    # Search discovery integration boundary. All NOT_ENABLED pending
    # the same phased, evidence-based live-validation process; zero of
    # them make a network call while NOT_ENABLED.
    "GREENHOUSE": GreenhouseAdapter,
    "LEVER": LeverAdapter,
    "ASHBY": AshbyAdapter,
    "WEB_SEARCH": WebSearchDiscoveryAdapter,
    # Phase 11 addition -- registered per explicit request; robots.txt
    # is permissive but the site's job data requires JS-rendering
    # infrastructure not built this phase. See apna_adapter.py.
    "APNA": ApnaAdapter,
    # Phase 14 -- search-provider discovery for the seven boards this
    # project has no authorized direct-crawl path for (see
    # restricted_source_registry.py and search_provider_adapter.py).
    # Deliberately separate keys from the Phase 4 "LINKEDIN"/"INDEED"/
    # etc. skeletons above (still NOT_ENABLED, still reserved for a
    # hypothetical future authorized DIRECT crawler) -- these adapters
    # still emit source="LINKEDIN" (etc.) on every job record; only the
    # REGISTRY key differs. ENABLED only when SERPER_API_KEY is
    # configured (see search_provider_adapter.py's status gate) --
    # otherwise NOT_ENABLED and zero network calls, exactly like every
    # other not-yet-configured source in this project.
    **SEARCH_PROVIDER_ADAPTERS,
}


def list_sources():
    return sorted(ADAPTERS.keys())


def board_name_for_source(registry_key):
    """The human-facing BOARD name (e.g. "LINKEDIN") for a registry key
    (e.g. "LINKEDIN_SEARCH" or "NAUKRI") -- the same distinction
    search_provider_adapter.py's own module docstring describes
    ("Registry key vs. raw-job source"): every *_SEARCH adapter still
    emits job records tagged with the real board name as
    `source`/`job_source`, only the REGISTRY key differs. A direct
    source's registry key already IS its board name (identity).
    Never a second, independently-maintained domain/board list --
    derived purely from the "*_SEARCH" naming convention every
    search-provider adapter registry key already follows (see
    search_provider_adapter.SEARCH_PROVIDER_ADAPTERS)."""
    registry_key = registry_key.upper()
    if registry_key.endswith("_SEARCH") and registry_key in SEARCH_PROVIDER_ADAPTERS:
        return registry_key[: -len("_SEARCH")]
    return registry_key


def get_adapter(source):
    source = source.upper()

    if source not in ADAPTERS:
        available = ", ".join(sorted(ADAPTERS))

        raise ValueError(
            f"Source '{source}' has no implemented adapter. "
            f"Implemented sources: {available}"
        )

    return ADAPTERS[source]()


def source_status(source):
    """
    Safe, non-networking introspection: whether a source is registered
    in ADAPTERS, and if so, its class-level AdapterStatus (read as a
    class attribute -- never instantiates the adapter). Never calls
    health_check() -- a real health check happens exactly once per
    source, per actual discovery run, inside discover_from_sources()
    only.

    "implemented"/"status" (REGISTERED/NOT_IMPLEMENTED) describe
    whether an adapter CLASS exists at all for this source name.
    "adapter_status" (added for Phase 4) separately describes whether
    that registered adapter is actually usable
    (AdapterStatus.ENABLED/NOT_ENABLED/REQUIRES_AUTH/BLOCKED/
    UNSUPPORTED) -- a source can be "REGISTERED" and still not
    ENABLED (every Phase 4 skeleton is exactly this).
    """
    source = source.upper()

    if source in ADAPTERS:
        return {
            "source": source,
            "implemented": True,
            "status": "REGISTERED",
            "adapter_status": ADAPTERS[source].status.value,
        }

    return {
        "source": source,
        "implemented": False,
        "status": "NOT_IMPLEMENTED",
        "adapter_status": None,
    }


def get_adapter_status(source):
    """
    Read-only, non-networking: the class-level AdapterStatus for a
    registered source, without instantiating it. Raises the same
    ValueError as get_adapter() for an unregistered source name.
    """
    source = source.upper()

    if source not in ADAPTERS:
        available = ", ".join(sorted(ADAPTERS))
        raise ValueError(
            f"Source '{source}' has no implemented adapter. "
            f"Implemented sources: {available}"
        )

    return ADAPTERS[source].status


def _coerce_query(query):
    """
    Accept either a legacy query dict ({"source", "role", "location", ...})
    or a (source, SearchQuery) tuple, and return (source, SearchQuery).

    Kept for backward compatibility with callers that still build plain
    dict queries (e.g. query_planner.py in its current, unmigrated form).
    """
    if (
        isinstance(query, tuple)
        and len(query) == 2
        and isinstance(query[1], SearchQuery)
    ):
        source, search_query = query
        return source, search_query

    if isinstance(query, dict):
        return query["source"], SearchQuery(
            role=query["role"],
            location=query["location"],
            experience_years=query.get("experience_years"),
            exclude_keywords=query.get("exclude_keywords", []),
            extra=query.get("extra", {}),
        )

    raise TypeError(
        "query must be a dict or a (source, SearchQuery) tuple, "
        f"got {type(query).__name__}"
    )


def _group_by_source(queries):
    grouped = {}
    order = []

    for query in queries:
        source, search_query = _coerce_query(query)

        if source not in grouped:
            grouped[source] = []
            order.append(source)

        grouped[source].append(search_query)

    return [(source, grouped[source]) for source in order]


def discover_from_sources(queries, run_report=None):
    """
    Execute queries grouped by source, honoring the Source Adapter
    Principle.

    Each source receives exactly one health_check() per call to this
    function, not once per query. For an adapter whose
    health_check_is_advisory is False (the default -- every adapter
    unless it explicitly opts in), a failed pre-flight health check is a
    hard gate: the source is skipped for the remainder of this call and
    is never retried or re-health-checked within the same call. Other
    sources continue unaffected.

    For an adapter whose health_check_is_advisory is True, a failed
    health check does NOT skip the source -- health_check()'s result is
    still recorded (state.health) for diagnostics, but every query is
    still attempted, and search() raising AdapterBlockedError while
    searching is what determines state.blocked from that point on. This
    exists because health_check() and search() can legitimately hit
    different URLs with different availability (see naukri_adapter.py's
    NaukriAdapter, the one adapter that currently opts in, and
    data/reports/naukri_health_check_architecture.md for the evidence
    that motivated it) -- a hard gate on the wrong URL can silently
    prevent every query from ever being attempted even when the actual
    query URL would have succeeded.

    Either way, an adapter's own search() raising AdapterBlockedError
    while searching skips the remainder of that source's queries for
    this call. An AdapterTimeoutError on one query is recorded as a
    query-level failure and does not stop the rest of that source's
    queries.

    Unknown/unimplemented sources raise an explicit error rather than
    silently being skipped.

    A REGISTERED adapter whose class-level `status` is not
    AdapterStatus.ENABLED (see source_adapter.AdapterStatus -- every
    Phase 4 skeleton adapter is NOT_ENABLED) is skipped BEFORE
    health_check() is ever called: zero network calls are made for
    such a source. This is a distinct, pre-flight, local fact-check --
    unrelated to the health_check_is_advisory flag above, which only
    matters once an adapter IS enabled. The corresponding
    SourceRunState has `not_enabled=True` and `adapter_status` set,
    and `blocked` stays False (a not-enabled source was never
    contacted, so it was never "blocked" by anything).

    queries: iterable of legacy dicts ({"source", "role", "location", ...})
    or (source, SearchQuery) tuples.

    run_report: optional list. If provided, one SourceRunState per
    source encountered is appended to it. Callers that omit this
    parameter see no change in behavior or return type.

    Returns: flat list of raw job dicts, exactly as before.
    """

    all_jobs = []

    for source, source_queries in _group_by_source(queries):
        adapter = get_adapter(source)

        adapter_status = getattr(adapter, "status", AdapterStatus.ENABLED)
        if adapter_status != AdapterStatus.ENABLED:
            state = SourceRunState(
                source=source,
                not_enabled=True,
                adapter_status=adapter_status,
            )
            if run_report is not None:
                run_report.append(state)
            continue

        source_started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        try:
            health = adapter.health_check()
        except Exception as error:
            # Same defensive boundary as the per-query loop below,
            # applied to health_check() itself -- it runs BEFORE `state`
            # is even constructed, so an adapter bug here previously
            # escaped uncaught just as easily, aborting the entire
            # multi-source batch before this source's queries (or any
            # later source) ever got a chance. No query was attempted,
            # so this is represented as blocked (this source could not
            # be used this run) rather than invented as a query-level
            # failure -- the real exception is still recorded in
            # unexpected_errors for honest diagnostics. Other sources
            # are unaffected: the outer for-loop simply continues.
            state = SourceRunState(source=source, started_at=source_started_at, blocked=True, blocked_reason=BlockReason.UNKNOWN_BLOCK)
            state.unexpected_errors.append(f"health_check() raised {type(error).__name__}: {error}")
            state.completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if run_report is not None:
                run_report.append(state)
            continue

        state = SourceRunState(source=source, health=health, started_at=source_started_at)

        health_failed = not (health.reachable and health.block_reason == BlockReason.NONE)

        if health_failed and not getattr(adapter, "health_check_is_advisory", False):
            state.blocked = True
            state.blocked_reason = health.block_reason
            state.completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

            if run_report is not None:
                run_report.append(state)

            continue

        # health_failed and advisory: deliberately do NOT set
        # state.blocked here -- see this function's docstring. The
        # per-query loop below is the sole source of truth for
        # blocked/succeeded/failed from this point on.

        for search_query in source_queries:
            state.queries_attempted += 1

            try:
                jobs = adapter.search(search_query)
                all_jobs.extend(jobs)
                state.jobs_found += len(jobs)
                state.queries_succeeded += 1

                # Optional adapter-reported extra facts (e.g. Apna's
                # detail-fetch counts) -- merged in verbatim, never
                # invented here. Later queries' details overwrite
                # earlier ones' for the same key (last-query-wins,
                # same as every other per-source aggregate in this
                # function being a running total across queries, not
                # a per-query breakdown).
                adapter_details = getattr(adapter, "last_search_details", None)
                if adapter_details:
                    state.extra_details.update(adapter_details)

            except AdapterBlockedError as error:
                state.blocked = True
                state.blocked_reason = error.reason
                break

            except AdapterTimeoutError:
                state.queries_failed += 1
                continue

            except Exception as error:
                # Defensive boundary (2026-09-28 hardening pass): an
                # adapter is EXPECTED to only ever raise
                # AdapterBlockedError/AdapterTimeoutError from search()
                # -- anything else is an adapter bug/gap (e.g. a raw
                # network exception an adapter forgot to wrap). Before
                # this boundary existed, such an exception propagated
                # straight out of this function, past every other
                # not-yet-attempted source, and aborted the ENTIRE
                # multi-source batch -- discarding jobs already found
                # by sources that had already completed successfully
                # earlier in this same call. Treated exactly like an
                # AdapterTimeoutError (isolated to this one query,
                # counted in queries_failed so _aggregate_status()'s
                # existing PARTIAL/FAILED logic already handles it
                # unchanged) rather than a silent continue: the real
                # exception is recorded in state.unexpected_errors for
                # diagnostics (search_worker.py folds this into the
                # run's error_message, same visibility as any other
                # query-level failure).
                state.queries_failed += 1
                state.unexpected_errors.append(f"{type(error).__name__}: {error}")
                continue

        state.completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if run_report is not None:
            run_report.append(state)

    return all_jobs


if __name__ == "__main__":
    print("SOURCE REGISTRY STATUS")
    print("======================")

    # The complete, agreed target source list (see CLAUDE.md's "Source
    # Adapter Principle" -> "Current adapter state"). Glassdoor is
    # deliberately absent -- it was added to documentation by mistake in
    # an earlier pass and was never part of the agreed roadmap.
    configured_sources = [
        "Naukri",
        "LinkedIn",
        "Hirist",
        "Indeed",
        "Foundit",
        "Instahyre",
        "Cutshort",
        "Wellfound",
        "Shine",
        "TimesJobs",
        "Iimjobs",
        "Career_Page",
    ]

    print("Configured source status:")
    print()

    for source in configured_sources:
        status = source_status(source)

        print(
            f"{status['source']:<12} | "
            f"implemented={status['implemented']} | "
            f"status={status['status']} | "
            f"adapter_status={status['adapter_status']}"
        )

    print()

    # Every source in the agreed roadmap is REGISTERED. Naukri (and
    # MOCK) are ENABLED; every other roadmap source is a registered,
    # NOT_ENABLED skeleton -- a real, intentional state, distinct from
    # "not registered at all" (an arbitrary, never-agreed-upon name,
    # checked separately below).
    mock = source_status("MOCK")
    naukri = source_status("NAUKRI")

    not_enabled_skeletons = [s for s in configured_sources if s != "Naukri"]
    not_registered_example = source_status("NOT_A_REAL_SOURCE")

    if (
        mock["implemented"]
        and mock["adapter_status"] == "ENABLED"
        and naukri["implemented"]
        and naukri["adapter_status"] == "ENABLED"
        and all(
            source_status(source)["implemented"]
            and source_status(source)["adapter_status"] == "NOT_ENABLED"
            for source in not_enabled_skeletons
        )
        and not not_registered_example["implemented"]
        and not_registered_example["adapter_status"] is None
    ):
        print(
            "PASS: registry correctly distinguishes ENABLED, "
            "registered-but-NOT_ENABLED, and not-registered-at-all sources."
        )
    else:
        print(
            "FAIL: registry source-status regression detected."
        )
        raise SystemExit(1)
