#!/usr/bin/env python3

"""
Regression tests for the Phase 4 multi-source adapter architecture:
AdapterStatus/AdapterCapability/AdapterNotEnabledError, the registry's
pre-flight NOT_ENABLED skip in discover_from_sources(), the eleven
skeleton adapters (LinkedIn, Hirist, Indeed, Foundit, Instahyre,
Cutshort, Wellfound, Shine, TimesJobs, iimjobs, generic career page --
the complete agreed target source list; Glassdoor is deliberately NOT
among them -- it was added to documentation by mistake and was removed),
search_profile._default_sources()'s ENABLED-only filtering, future
source extensibility (a newly-registered NOT_ENABLED adapter never
enters a default search plan), and cross-source dedup's Bangalore/
Bengaluru handling.

Makes NO live network/browser request of any kind. Cleans up every
ADAPTERS entry it adds, exactly like test_advisory_health_check.py /
test_search_worker.py / test_max_job_age_days.py already do.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from source_adapter import (
    JobSourceAdapter,
    SearchQuery,
    AdapterStatus,
    AdapterCapability,
    AdapterNotEnabledError,
    SourceHealthRecord,
    SourceRunState,
    MockJobSourceAdapter,
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
import source_registry
import search_profile
import search_worker
from canonical_job import derive_canonical_job
from cross_source_dedup import find_cross_source_duplicate_candidates
from discover_local import normalize_job


SKELETON_CLASSES = {
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
}

# An arbitrary, deliberately fictional source name -- used only to
# prove "not registered at all" behavior. Not Glassdoor: Glassdoor was
# removed from this project's roadmap entirely (added to documentation
# by mistake previously) and must not appear anywhere, including as a
# test fixture, to avoid re-implying it is part of the agreed list.
_UNREGISTERED_SOURCE_NAME = "NOT_A_REAL_SOURCE"


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def test_adapter_interface_shapes(failures):
    """1. Adapter interface: AdapterStatus/AdapterCapability/
    AdapterNotEnabledError exist and are correctly shaped."""
    if not hasattr(AdapterStatus, "ENABLED") or not hasattr(AdapterStatus, "NOT_ENABLED"):
        return _fail(failures, "AdapterStatus missing ENABLED/NOT_ENABLED")

    expected_capabilities = {
        "SEARCH", "DETAIL", "NATIVE_FRESHNESS", "PAGINATION",
        "APPLICATION_URL", "COMPANY_METADATA", "SALARY",
        "REMOTE_FILTER", "LOCATION_FILTER",
    }
    actual = {c.name for c in AdapterCapability}
    if actual != expected_capabilities:
        return _fail(failures, f"AdapterCapability members mismatch: {actual}")

    error = AdapterNotEnabledError("LINKEDIN", AdapterStatus.NOT_ENABLED, detail="test")
    if error.source != "LINKEDIN" or error.status != AdapterStatus.NOT_ENABLED:
        return _fail(failures, "AdapterNotEnabledError did not store source/status correctly")

    if JobSourceAdapter.status != AdapterStatus.ENABLED:
        return _fail(failures, "JobSourceAdapter base class default status is not ENABLED")

    if JobSourceAdapter.capabilities != frozenset():
        return _fail(failures, "JobSourceAdapter base class default capabilities is not empty")

    print("PASS: adapter interface shapes (AdapterStatus/AdapterCapability/AdapterNotEnabledError)")


def test_source_registration(failures):
    """2. Source registration: all 11 skeleton sources (the complete
    agreed target list minus Naukri) are registered, and Glassdoor is
    confirmed absent."""
    expected = set(SKELETON_CLASSES.keys())
    registered = set(source_registry.list_sources())
    missing = expected - registered
    if missing:
        return _fail(failures, f"new sources not registered: {missing}")

    if "NAUKRI" not in registered or "MOCK" not in registered:
        return _fail(failures, "existing NAUKRI/MOCK registration was lost")

    if "GLASSDOOR" in registered:
        return _fail(failures, "GLASSDOOR is registered -- it was explicitly removed from the agreed roadmap")

    print(f"PASS: all {len(expected)} skeleton sources registered, existing MOCK/NAUKRI unaffected, GLASSDOOR confirmed absent")


def test_capability_declarations(failures):
    """3. Capability declarations: Naukri/Mock have real capabilities;
    skeletons declare none."""
    if not NaukriAdapter.capabilities:
        return _fail(failures, "NaukriAdapter.capabilities is empty -- expected real declared capabilities")

    if AdapterCapability.PAGINATION in NaukriAdapter.capabilities:
        return _fail(failures, "NaukriAdapter declares PAGINATION, but it only supports page 1")

    if MockJobSourceAdapter.capabilities != frozenset({AdapterCapability.SEARCH}):
        return _fail(failures, "MockJobSourceAdapter.capabilities is not exactly {SEARCH}")

    # HIRIST is no longer a bare skeleton as of Phase 11 (see this
    # file's own HIRIST-specific block further down) -- it correctly
    # declares real capabilities now, exactly like Naukri/Mock.
    for source, cls in SKELETON_CLASSES.items():
        if source in ("HIRIST", "IIMJOBS"):
            continue
        if cls.capabilities != frozenset():
            return _fail(failures, f"{source} skeleton declares non-empty capabilities: {cls.capabilities}")

    print("PASS: capability declarations correct (Naukri/Mock/Hirist real, remaining skeletons empty)")


def test_unsupported_adapter_behavior(failures):
    """4. Unsupported adapter behavior: a BARE skeleton's health_check()/
    search() raise AdapterNotEnabledError immediately, zero network
    calls (trivially true -- no browser/subprocess/HTTP import is even
    reachable in those modules).

    HIRIST is deliberately excluded from this specific loop as of
    Phase 6 Step 3 (data/reports/hirist_phase6_adapter_implementation.md):
    it is no longer a bare skeleton that raises immediately -- it has
    real, offline-tested parsing/pagination logic (see
    hirist_parser.py, test_hirist_adapter.py). It remains NOT_ENABLED
    via the class-level status/registry pre-flight gate (still
    verified below, and by test_capability_declarations /
    test_source_registration elsewhere in this file), not via an
    immediate raise -- so calling health_check()/search() on it
    directly now reaches HiristAdapter's own safety backstop instead
    (HiristFetcher.fetch() raising NotImplementedError, asserted
    separately below), never AdapterNotEnabledError. This is a real,
    intentional behavior change for this one adapter, not a test
    weakening -- HIRIST's NOT_ENABLED status, empty capabilities, and
    exclusion from the default search plan are all still independently
    verified elsewhere in this file."""
    query = SearchQuery(role="Test Role", location="Test Location")

    bare_skeletons = {source: cls for source, cls in SKELETON_CLASSES.items() if source not in ("HIRIST", "IIMJOBS")}

    for source, cls in bare_skeletons.items():
        adapter = cls()

        if adapter.status != AdapterStatus.NOT_ENABLED:
            _fail(failures, f"{source} skeleton .status is not NOT_ENABLED")
            continue

        try:
            adapter.health_check()
            _fail(failures, f"{source} skeleton health_check() did not raise")
            continue
        except AdapterNotEnabledError as error:
            if error.source != source:
                _fail(failures, f"{source} skeleton health_check() raised with wrong source: {error.source}")

        try:
            adapter.search(query)
            _fail(failures, f"{source} skeleton search() did not raise")
            continue
        except AdapterNotEnabledError as error:
            if error.source != source:
                _fail(failures, f"{source} skeleton search() raised with wrong source: {error.source}")

    if not failures:
        print(f"PASS: every bare skeleton adapter ({len(bare_skeletons)} of {len(SKELETON_CLASSES)}) raises AdapterNotEnabledError from health_check() and search()")

    # HIRIST-specific: as of Phase 11, HiristAdapter passed the full
    # live-validation enablement gate (data/reports/
    # phase11_public_multisource_completion.md) and is genuinely
    # AdapterStatus.ENABLED -- it is intentionally excluded from the
    # bare_skeletons loop above (still true) and from the NOT_ENABLED
    # assertion this block used to make. This test therefore does NOT
    # call health_check()/search() on a default-constructed
    # HiristAdapter (that would be a real, live network attempt) -- it
    # only verifies the class-level gate reflects Phase 11's decision.
    from hirist_adapter import HiristAdapter as _HiristAdapter

    hirist_adapter = _HiristAdapter()
    expected_hirist_caps = {AdapterCapability.SEARCH, AdapterCapability.DETAIL, AdapterCapability.PAGINATION}
    if hirist_adapter.status != AdapterStatus.ENABLED:
        _fail(failures, "HIRIST adapter .status is not ENABLED (expected, per Phase 11)")
    elif hirist_adapter.capabilities != expected_hirist_caps:
        _fail(failures, f"HIRIST adapter .capabilities = {hirist_adapter.capabilities}, expected {expected_hirist_caps}")
    else:
        print("PASS: HIRIST is genuinely ENABLED with {SEARCH, DETAIL, PAGINATION} capabilities, per Phase 11's passed live-validation gate")


def test_adapter_selection(failures):
    """5. Adapter selection: get_adapter() returns the correct class
    instance for every new source, and rejects an unregistered name."""
    for source, cls in SKELETON_CLASSES.items():
        adapter = source_registry.get_adapter(source)
        if not isinstance(adapter, cls):
            _fail(failures, f"get_adapter({source!r}) did not return a {cls.__name__} instance")

    try:
        source_registry.get_adapter(_UNREGISTERED_SOURCE_NAME)
        _fail(failures, f"get_adapter({_UNREGISTERED_SOURCE_NAME!r}) did not raise for an unregistered source")
    except ValueError:
        pass

    if not failures:
        print(f"PASS: adapter selection correct for all {len(SKELETON_CLASSES)} skeleton sources; unregistered source still rejected")


def test_default_sources_excludes_not_enabled(failures):
    """6. search_profile._default_sources() excludes NOT_ENABLED
    skeletons -- the exact regression this architecture change was
    designed to prevent (a candidate's default search plan silently
    growing to include unimplemented sources). Phase 11 note: HIRIST
    is now genuinely ENABLED (a real, evidence-based product change,
    not a regression) and so is CORRECTLY included below; every other
    skeleton (checked via LINKEDIN, still NOT_ENABLED) must remain
    excluded -- that is the actual property this test protects.
    IIMJOBS is also now genuinely ENABLED (Phase 11, new adapter --
    shares Hirist's platform)."""
    # Subset, not exact equality: _default_sources() also includes any
    # search-provider-backed source (*_SEARCH) ENABLED in THIS
    # environment's .env, correctly (see search_provider.py's
    # root-cause .env-loading fix). The direct-crawl "LINKEDIN"
    # skeleton key specifically must still be excluded -- checked below.
    defaults = search_profile._default_sources()
    if not {"NAUKRI", "HIRIST", "IIMJOBS", "APNA"} <= set(defaults):
        return _fail(
            failures,
            f"_default_sources() returned {defaults!r}, expected it to include at least "
            "{'NAUKRI', 'HIRIST', 'IIMJOBS', 'APNA'} (the genuinely-ENABLED direct sources)",
        )
    if "LINKEDIN" in defaults:
        return _fail(failures, "_default_sources() incorrectly included still-NOT_ENABLED LINKEDIN")

    print("PASS: search_profile._default_sources() == {'NAUKRI', 'HIRIST', 'IIMJOBS', 'APNA'} (still-NOT_ENABLED skeletons like LINKEDIN remain excluded)")


def test_source_status_reports_adapter_status(failures):
    """7. source_status()/get_adapter_status() correctly distinguish
    ENABLED vs NOT_ENABLED vs not-registered-at-all."""
    naukri = source_registry.source_status("NAUKRI")
    if naukri["adapter_status"] != "ENABLED":
        return _fail(failures, f"source_status('NAUKRI')['adapter_status'] = {naukri['adapter_status']!r}, expected 'ENABLED'")

    linkedin = source_registry.source_status("LINKEDIN")
    if not linkedin["implemented"] or linkedin["adapter_status"] != "NOT_ENABLED":
        return _fail(failures, f"source_status('LINKEDIN') = {linkedin}, expected implemented=True, adapter_status='NOT_ENABLED'")

    not_a_real_source = source_registry.source_status(_UNREGISTERED_SOURCE_NAME)
    if not_a_real_source["implemented"] or not_a_real_source["adapter_status"] is not None:
        return _fail(
            failures,
            f"source_status({_UNREGISTERED_SOURCE_NAME!r}) = {not_a_real_source}, "
            "expected implemented=False, adapter_status=None",
        )

    glassdoor = source_registry.source_status("GLASSDOOR")
    if glassdoor["implemented"]:
        return _fail(
            failures,
            "GLASSDOOR is registered/implemented -- it was explicitly removed "
            "from the agreed roadmap and must remain unregistered",
        )

    if source_registry.get_adapter_status("NAUKRI") != AdapterStatus.ENABLED:
        return _fail(failures, "get_adapter_status('NAUKRI') != ENABLED")

    if source_registry.get_adapter_status("LINKEDIN") != AdapterStatus.NOT_ENABLED:
        return _fail(failures, "get_adapter_status('LINKEDIN') != NOT_ENABLED")

    print("PASS: source_status()/get_adapter_status() correctly distinguish ENABLED/NOT_ENABLED/unregistered")


def test_discover_from_sources_skips_not_enabled_with_zero_calls(failures):
    """8. Worker source iteration: discover_from_sources() processes an
    ENABLED fake source normally, and skips a NOT_ENABLED source with
    ZERO calls to its health_check()/search() -- the core "for each
    enabled source... but do not run against new live sources yet"
    guarantee this architecture provides."""

    call_log = []

    class _FakeEnabledAdapter(JobSourceAdapter):
        name = "FAKE_ENABLED_MS"
        status = AdapterStatus.ENABLED
        capabilities = frozenset({AdapterCapability.SEARCH})

        def health_check(self):
            call_log.append("health_check")
            from source_adapter import AdapterHealth, BlockReason
            return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

        def search(self, query):
            call_log.append(f"search:{query.location}")
            return [
                {
                    "source": self.name,
                    "company": "Fake Co",
                    "title": query.role,
                    "location": query.location,
                    "job_url": f"https://example.com/{query.location}",
                }
            ]

    class _SpyNotEnabledAdapter(JobSourceAdapter):
        name = "FAKE_NOT_ENABLED_MS"
        status = AdapterStatus.NOT_ENABLED
        capabilities = frozenset()

        def health_check(self):
            call_log.append("SHOULD_NEVER_BE_CALLED_health_check")
            raise AssertionError("health_check() must never be called for a NOT_ENABLED source")

        def search(self, query):
            call_log.append("SHOULD_NEVER_BE_CALLED_search")
            raise AssertionError("search() must never be called for a NOT_ENABLED source")

    source_registry.ADAPTERS["FAKE_ENABLED_MS"] = _FakeEnabledAdapter
    source_registry.ADAPTERS["FAKE_NOT_ENABLED_MS"] = _SpyNotEnabledAdapter

    try:
        run_report = []
        queries = [
            ("FAKE_ENABLED_MS", SearchQuery(role="Role A", location="LocA")),
            ("FAKE_NOT_ENABLED_MS", SearchQuery(role="Role B", location="LocB")),
        ]
        jobs = source_registry.discover_from_sources(queries, run_report=run_report)

        if len(jobs) != 1 or jobs[0]["source"] != "FAKE_ENABLED_MS":
            _fail(failures, f"expected exactly 1 job from the enabled fake source, got {jobs}")

        if any(entry.startswith("SHOULD_NEVER_BE_CALLED") for entry in call_log):
            _fail(failures, f"a NOT_ENABLED adapter's health_check()/search() was actually called: {call_log}")

        states_by_source = {state.source: state for state in run_report}

        enabled_state = states_by_source.get("FAKE_ENABLED_MS")
        if enabled_state is None or enabled_state.not_enabled or enabled_state.queries_succeeded != 1:
            _fail(failures, f"enabled source's SourceRunState is wrong: {enabled_state}")

        not_enabled_state = states_by_source.get("FAKE_NOT_ENABLED_MS")
        if (
            not_enabled_state is None
            or not not_enabled_state.not_enabled
            or not_enabled_state.adapter_status != AdapterStatus.NOT_ENABLED
            or not_enabled_state.blocked
            or not_enabled_state.queries_attempted != 0
        ):
            _fail(failures, f"not-enabled source's SourceRunState is wrong: {not_enabled_state}")

        if not failures:
            print("PASS: discover_from_sources() runs the enabled source normally and skips the NOT_ENABLED source with zero calls")
    finally:
        del source_registry.ADAPTERS["FAKE_ENABLED_MS"]
        del source_registry.ADAPTERS["FAKE_NOT_ENABLED_MS"]


def test_naukri_compatibility_unchanged(failures):
    """9. Naukri compatibility: NaukriAdapter's validated behavior/flags
    are unchanged by this architecture work."""
    adapter = NaukriAdapter()

    if adapter.status != AdapterStatus.ENABLED:
        return _fail(failures, "NaukriAdapter.status is not ENABLED -- Naukri must remain the one enabled live source")

    if adapter.health_check_is_advisory is not True:
        return _fail(failures, "NaukriAdapter.health_check_is_advisory changed -- must remain True")

    if not {AdapterCapability.SEARCH, AdapterCapability.DETAIL, AdapterCapability.NATIVE_FRESHNESS}.issubset(
        NaukriAdapter.capabilities
    ):
        return _fail(failures, "NaukriAdapter.capabilities is missing an expected, already-validated capability")

    print("PASS: NaukriAdapter status/health_check_is_advisory/capabilities all consistent with its validated behavior")


def test_existing_query_snapshot_compatibility(failures):
    """10. Existing query snapshot compatibility: an old-shape snapshot
    (no new fields) still reconstructs correctly via
    search_worker.build_queries_from_snapshot() -- this architecture
    change introduced no new REQUIRED snapshot field."""
    old_shape_snapshot = {
        "queries": [
            {"source": "NAUKRI", "role": "Infrastructure Engineer", "location": "Pune"},
        ]
    }

    queries, unimplemented, totals = search_worker.build_queries_from_snapshot(old_shape_snapshot)

    if len(queries) != 1 or queries[0][0] != "NAUKRI":
        return _fail(failures, f"old-shape snapshot did not reconstruct correctly: {queries}")

    search_query = queries[0][1]
    if search_query.role != "Infrastructure Engineer" or search_query.location != "Pune" or search_query.max_job_age_days is not None:
        return _fail(failures, f"old-shape snapshot's SearchQuery fields are wrong: {search_query}")

    print("PASS: old-shape (pre-Phase-4) search_run snapshot still reconstructs correctly")


def test_cross_source_dedup_bangalore_bengaluru(failures):
    """11. Cross-source deduplication: a job posted on two different
    sources under 'Bangalore' vs 'Bengaluru' is correctly flagged as a
    duplicate CANDIDATE (canonical_location collapses both to the same
    city -- see location_taxonomy.py), and a genuinely different job at
    the same company/title but a different city is correctly NOT
    flagged."""
    # Company name deliberately avoids "Corp"/"Inc"/"Ltd"-family suffix
    # combinations beyond a single one: canonical_job.normalize_company()
    # only strips ONE trailing legal suffix by design (see its own
    # docstring/_LEGAL_SUFFIXES) -- "Example Robotics Private Limited"
    # vs "Example Robotics" both normalize to "example robotics",
    # exercising the case this test cares about (location canonicali-
    # zation) without tripping over that separate, existing, unrelated
    # single-suffix-stripping behavior.
    naukri_job = normalize_job(
        {
            "source": "NAUKRI",
            "company": "Example Robotics Private Limited",
            "title": "Senior DevOps Engineer",
            "location": "Bangalore",
            "job_url": "https://naukri.com/job/1",
            "jd_text": "We need a senior devops engineer with AWS, Terraform, Kubernetes experience for our platform team.",
        },
        1,
    )
    linkedin_job = normalize_job(
        {
            "source": "LINKEDIN",
            "company": "Example Robotics",
            "title": "Senior DevOps Engineer",
            "location": "Bengaluru",
            "job_url": "https://linkedin.com/jobs/view/2",
            "jd_text": "We need a senior devops engineer with AWS, Terraform, Kubernetes experience for our platform team.",
        },
        2,
    )
    different_city_job = normalize_job(
        {
            "source": "LINKEDIN",
            "company": "Example Robotics",
            "title": "Senior DevOps Engineer",
            "location": "Chennai",
            "job_url": "https://linkedin.com/jobs/view/3",
            "jd_text": "Completely unrelated posting text about a different opening in a different city.",
        },
        3,
    )

    canonical_jobs = [
        derive_canonical_job(naukri_job),
        derive_canonical_job(linkedin_job),
        derive_canonical_job(different_city_job),
    ]

    if canonical_jobs[0].canonical_location != canonical_jobs[1].canonical_location:
        return _fail(
            failures,
            f"Bangalore/Bengaluru did not canonicalize to the same city: "
            f"{canonical_jobs[0].canonical_location!r} vs {canonical_jobs[1].canonical_location!r}",
        )

    candidates = find_cross_source_duplicate_candidates(canonical_jobs)

    matched_pair = [
        c for c in candidates
        if {c.job_a.source, c.job_b.source} == {"NAUKRI", "LINKEDIN"}
        and {c.job_a.source_job_id, c.job_b.source_job_id} == {naukri_job["job_id"], linkedin_job["job_id"]}
    ]
    if not matched_pair:
        return _fail(failures, f"Naukri/LinkedIn Bangalore-vs-Bengaluru pair was NOT flagged as a duplicate candidate: {candidates}")

    unrelated_flagged = [
        c for c in candidates
        if different_city_job["job_id"] in (c.job_a.source_job_id, c.job_b.source_job_id)
    ]
    if unrelated_flagged:
        return _fail(failures, f"the different-city job was incorrectly flagged as a duplicate candidate: {unrelated_flagged}")

    # Same-source pairs are explicitly out of scope for this module
    # (discover_local.deduplicate() already owns exact intra-source
    # dedup) -- confirm that contract still holds.
    same_source_a = normalize_job(
        {"source": "NAUKRI", "company": "X", "title": "Y", "location": "Bangalore", "job_url": "https://naukri.com/a"}, 4
    )
    same_source_b = normalize_job(
        {"source": "NAUKRI", "company": "X", "title": "Y", "location": "Bangalore", "job_url": "https://naukri.com/b"}, 5
    )
    same_source_candidates = find_cross_source_duplicate_candidates(
        [derive_canonical_job(same_source_a), derive_canonical_job(same_source_b)]
    )
    if same_source_candidates:
        return _fail(failures, "find_cross_source_duplicate_candidates() incorrectly compared two same-source jobs")

    print("PASS: cross-source dedup correctly flags Bangalore/Bengaluru duplicates, rejects a genuinely different job, and skips same-source pairs")


def test_source_health_record_no_fabricated_data(failures):
    """12. Source health model: SourceHealthRecord exists with the
    requested fields, and constructing one for a never-tested source
    produces "no data" (None/0/empty), never a fabricated-looking
    value."""
    record = SourceHealthRecord(source="LINKEDIN")

    if record.status != AdapterStatus.NOT_ENABLED:
        return _fail(failures, f"SourceHealthRecord default status is {record.status}, expected NOT_ENABLED")

    for field_name in ("last_check_at", "last_success_at", "last_failure_at", "error_type", "http_status", "classifier"):
        if getattr(record, field_name) is not None:
            return _fail(failures, f"SourceHealthRecord.{field_name} was not None by default: {getattr(record, field_name)!r}")

    if record.query_count != 0 or record.job_count != 0 or record.latency_seconds is not None:
        return _fail(failures, "SourceHealthRecord's count/latency fields were not zero/None by default")

    print("PASS: SourceHealthRecord fabricates no data for a never-tested source")


def test_full_regression_suite_files_unaffected(failures):
    """13. Sanity: the files this task touched all still import
    cleanly together (belt-and-suspenders on top of the separate
    py_compile pass and full suite run already performed for this
    task)."""
    import importlib

    for module_name in [
        "source_adapter", "source_registry", "naukri_adapter",
        "linkedin_adapter", "hirist_adapter", "indeed_adapter",
        "foundit_adapter", "instahyre_adapter", "cutshort_adapter",
        "wellfound_adapter", "shine_adapter", "timesjobs_adapter",
        "iimjobs_adapter", "career_page_adapter",
        "search_profile", "search_worker",
    ]:
        try:
            importlib.reload(sys.modules[module_name]) if module_name in sys.modules else importlib.import_module(module_name)
        except Exception as error:  # noqa: BLE001
            return _fail(failures, f"module {module_name!r} failed to import/reload cleanly: {error}")

    print("PASS: all Phase 4 touched modules import/reload cleanly together")


def test_future_extensibility_new_not_enabled_adapter_excluded(failures):
    """14. Future extensibility: registering a BRAND NEW adapter with
    status NOT_ENABLED (simulating a source added after this test suite
    was written) must NOT automatically make it part of a candidate's
    default search plan, and discover_from_sources() must still skip it
    with zero network calls -- the guarantee must hold generically, not
    just for the specific sources hardcoded elsewhere in this file."""

    class _FutureAdapter(JobSourceAdapter):
        name = "FUTURE_SOURCE_NOT_YET_BUILT"
        status = AdapterStatus.NOT_ENABLED
        capabilities = frozenset()

        def health_check(self):
            raise AssertionError("health_check() must never be called for a NOT_ENABLED source")

        def search(self, query):
            raise AssertionError("search() must never be called for a NOT_ENABLED source")

    source_registry.ADAPTERS["FUTURE_SOURCE_NOT_YET_BUILT"] = _FutureAdapter

    try:
        defaults = search_profile._default_sources()
        if "FUTURE_SOURCE_NOT_YET_BUILT" in defaults:
            _fail(failures, f"newly-registered NOT_ENABLED adapter leaked into _default_sources(): {defaults}")

        run_report = []
        jobs = source_registry.discover_from_sources(
            [("FUTURE_SOURCE_NOT_YET_BUILT", SearchQuery(role="R", location="L"))],
            run_report=run_report,
        )
        if jobs:
            _fail(failures, f"discover_from_sources() returned jobs for a NOT_ENABLED source: {jobs}")

        if not run_report or not run_report[0].not_enabled:
            _fail(failures, f"run_report did not correctly mark the new source not_enabled: {run_report}")

        if not failures:
            print("PASS: a brand-new NOT_ENABLED adapter is excluded from the default search plan and skipped with zero calls")
    finally:
        del source_registry.ADAPTERS["FUTURE_SOURCE_NOT_YET_BUILT"]


def test_discover_from_sources_isolates_unexpected_query_exception(failures):
    """15. Exception-boundary hardening (2026-09-28): a query raising an
    exception OUTSIDE the AdapterBlockedError/AdapterTimeoutError
    contract (an adapter bug/gap, e.g. an unwrapped network exception)
    must be isolated to that one query -- not abort the entire batch.

    query 1 (LocA) -> success -> jobs A/B
    query 2 (LocB) -> raises unexpected RuntimeError
    query 3 (LocC) -> success -> jobs C/D

    Expected: returned jobs contain A/B/C/D, query 2 has an error
    record, query 3 still executes, no whole-batch exception escapes,
    and the aggregated status reflects a partial/failed-query condition."""

    class _FlakyAdapter(JobSourceAdapter):
        name = "FAKE_FLAKY_MS"
        status = AdapterStatus.ENABLED
        capabilities = frozenset({AdapterCapability.SEARCH})

        def health_check(self):
            from source_adapter import AdapterHealth, BlockReason
            return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

        def search(self, query):
            if query.location == "LocB":
                raise RuntimeError("simulated adapter bug -- unwrapped exception")
            suffix = "AB" if query.location == "LocA" else "CD"
            return [
                {"source": self.name, "company": "Fake Co", "title": query.role, "location": query.location, "job_url": f"https://example.com/{suffix}1"},
                {"source": self.name, "company": "Fake Co", "title": query.role, "location": query.location, "job_url": f"https://example.com/{suffix}2"},
            ]

    source_registry.ADAPTERS["FAKE_FLAKY_MS"] = _FlakyAdapter

    try:
        run_report = []
        queries = [
            ("FAKE_FLAKY_MS", SearchQuery(role="R", location="LocA")),
            ("FAKE_FLAKY_MS", SearchQuery(role="R", location="LocB")),
            ("FAKE_FLAKY_MS", SearchQuery(role="R", location="LocC")),
        ]
        try:
            jobs = source_registry.discover_from_sources(queries, run_report=run_report)
        except Exception as error:
            _fail(failures, f"15: an unexpected query exception escaped discover_from_sources() entirely: {type(error).__name__}: {error}")
            return

        urls = {job["job_url"] for job in jobs}
        expected_urls = {"https://example.com/AB1", "https://example.com/AB2", "https://example.com/CD1", "https://example.com/CD2"}
        if urls != expected_urls:
            _fail(failures, f"15: expected jobs A/B/C/D preserved despite query 2's exception, got job_urls={urls}")

        state = next((s for s in run_report if s.source == "FAKE_FLAKY_MS"), None)
        if state is None:
            _fail(failures, "15: no SourceRunState recorded for FAKE_FLAKY_MS")
        else:
            if state.queries_succeeded != 2:
                _fail(failures, f"15: expected 2 succeeded queries (LocA, LocC), got {state.queries_succeeded}")
            if state.queries_failed != 1:
                _fail(failures, f"15: expected 1 failed query (LocB), got {state.queries_failed}")
            if len(state.unexpected_errors) != 1 or "RuntimeError" not in state.unexpected_errors[0]:
                _fail(failures, f"15: expected exactly 1 unexpected_errors entry mentioning RuntimeError, got {state.unexpected_errors}")
            if state.blocked:
                _fail(failures, "15: an unexpected query exception must not be conflated with AdapterBlockedError (state.blocked)")

        from search_worker import _aggregate_status
        status = _aggregate_status(run_report, had_unexpected_exception=False, any_query_attempted=True)
        if status != "PARTIAL":
            _fail(failures, f"15: expected aggregated status PARTIAL (2 succeeded + 1 unexpected-exception query), got {status}")

        if not failures:
            print("PASS: 15 -> query 2's unexpected RuntimeError is isolated; jobs A/B/C/D preserved, query 3 still executes, status is PARTIAL")
    finally:
        del source_registry.ADAPTERS["FAKE_FLAKY_MS"]


def test_discover_from_sources_isolates_multiple_unexpected_exceptions(failures):
    """16. Same hardening, harsher case:

    query 1 -> success
    query 2 -> unexpected exception (RuntimeError)
    query 3 -> unexpected exception (a DIFFERENT type, ValueError --
               proves this isn't RuntimeError-specific)

    Expected: query 1's successful results are still returned and both
    failures are accurately reported -- no crash, even though only one
    of three queries actually succeeded."""

    class _MostlyBrokenAdapter(JobSourceAdapter):
        name = "FAKE_MOSTLY_BROKEN_MS"
        status = AdapterStatus.ENABLED
        capabilities = frozenset({AdapterCapability.SEARCH})

        def health_check(self):
            from source_adapter import AdapterHealth, BlockReason
            return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

        def search(self, query):
            if query.location == "LocOK":
                return [{"source": self.name, "company": "Fake Co", "title": query.role, "location": query.location, "job_url": "https://example.com/ok"}]
            if query.location == "LocRuntimeError":
                raise RuntimeError("simulated adapter bug 1")
            raise ValueError("simulated adapter bug 2 -- a different exception type")

    source_registry.ADAPTERS["FAKE_MOSTLY_BROKEN_MS"] = _MostlyBrokenAdapter

    try:
        run_report = []
        queries = [
            ("FAKE_MOSTLY_BROKEN_MS", SearchQuery(role="R", location="LocOK")),
            ("FAKE_MOSTLY_BROKEN_MS", SearchQuery(role="R", location="LocRuntimeError")),
            ("FAKE_MOSTLY_BROKEN_MS", SearchQuery(role="R", location="LocValueError")),
        ]
        try:
            jobs = source_registry.discover_from_sources(queries, run_report=run_report)
        except Exception as error:
            _fail(failures, f"16: an unexpected query exception escaped discover_from_sources() entirely: {type(error).__name__}: {error}")
            return

        if [job["job_url"] for job in jobs] != ["https://example.com/ok"]:
            _fail(failures, f"16: expected only query 1's single successful job preserved, got {jobs}")

        state = next((s for s in run_report if s.source == "FAKE_MOSTLY_BROKEN_MS"), None)
        if state is None:
            _fail(failures, "16: no SourceRunState recorded for FAKE_MOSTLY_BROKEN_MS")
        else:
            if state.queries_succeeded != 1 or state.queries_failed != 2:
                _fail(failures, f"16: expected 1 succeeded / 2 failed, got succeeded={state.queries_succeeded} failed={state.queries_failed}")
            if len(state.unexpected_errors) != 2:
                _fail(failures, f"16: expected 2 unexpected_errors entries (one per distinct exception type), got {state.unexpected_errors}")

        from search_worker import _aggregate_status
        status = _aggregate_status(run_report, had_unexpected_exception=False, any_query_attempted=True)
        if status != "PARTIAL":
            _fail(failures, f"16: expected aggregated status PARTIAL (1 succeeded + 2 unexpected-exception queries), got {status}")

        if not failures:
            print("PASS: 16 -> two DIFFERENT unexpected exception types (RuntimeError, ValueError) are both isolated; query 1's result survives, both failures are accurately reported")
    finally:
        del source_registry.ADAPTERS["FAKE_MOSTLY_BROKEN_MS"]


def test_discover_from_sources_isolates_health_check_exception(failures):
    """17. The same defensive boundary applied to health_check() itself
    (which runs BEFORE the per-query loop, for each source): a source
    whose health_check() raises unexpectedly must not prevent OTHER
    sources from being searched, and results already gathered from an
    earlier-completed source must not be discarded."""

    class _HealthCheckCrashesAdapter(JobSourceAdapter):
        name = "FAKE_HEALTH_CRASH_MS"
        status = AdapterStatus.ENABLED
        capabilities = frozenset({AdapterCapability.SEARCH})

        def health_check(self):
            raise RuntimeError("simulated health_check() bug")

        def search(self, query):
            raise AssertionError("search() must never be called when health_check() itself crashed")

    class _NormalAdapter(JobSourceAdapter):
        name = "FAKE_HEALTH_OK_MS"
        status = AdapterStatus.ENABLED
        capabilities = frozenset({AdapterCapability.SEARCH})

        def health_check(self):
            from source_adapter import AdapterHealth, BlockReason
            return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

        def search(self, query):
            return [{"source": self.name, "company": "Fake Co", "title": query.role, "location": query.location, "job_url": "https://example.com/healthy"}]

    source_registry.ADAPTERS["FAKE_HEALTH_CRASH_MS"] = _HealthCheckCrashesAdapter
    source_registry.ADAPTERS["FAKE_HEALTH_OK_MS"] = _NormalAdapter

    try:
        run_report = []
        # The crashing source is queried FIRST, deliberately, so this
        # also proves an earlier source's crash does not discard a
        # LATER source's genuine results within the same call.
        queries = [
            ("FAKE_HEALTH_CRASH_MS", SearchQuery(role="R", location="LocX")),
            ("FAKE_HEALTH_OK_MS", SearchQuery(role="R", location="LocY")),
        ]
        try:
            jobs = source_registry.discover_from_sources(queries, run_report=run_report)
        except Exception as error:
            _fail(failures, f"17: an unexpected health_check() exception escaped discover_from_sources() entirely: {type(error).__name__}: {error}")
            return

        if [job["job_url"] for job in jobs] != ["https://example.com/healthy"]:
            _fail(failures, f"17: expected the healthy source's job preserved despite the other source's health_check() crash, got {jobs}")

        crash_state = next((s for s in run_report if s.source == "FAKE_HEALTH_CRASH_MS"), None)
        if crash_state is None or crash_state.queries_attempted != 0 or not crash_state.unexpected_errors:
            _fail(failures, f"17: expected the crashed source recorded with zero queries attempted and a non-empty unexpected_errors, got {crash_state}")

        healthy_state = next((s for s in run_report if s.source == "FAKE_HEALTH_OK_MS"), None)
        if healthy_state is None or healthy_state.queries_succeeded != 1:
            _fail(failures, f"17: expected the healthy source's own state unaffected, got {healthy_state}")

        if not failures:
            print("PASS: 17 -> one source's health_check() crash is isolated to that source; the other source's genuine results survive")
    finally:
        del source_registry.ADAPTERS["FAKE_HEALTH_CRASH_MS"]
        del source_registry.ADAPTERS["FAKE_HEALTH_OK_MS"]


def main():
    print("MULTI-SOURCE ADAPTER ARCHITECTURE TEST (Phase 4)")
    print("===================================================")

    failures = []

    test_adapter_interface_shapes(failures)
    test_source_registration(failures)
    test_capability_declarations(failures)
    test_unsupported_adapter_behavior(failures)
    test_adapter_selection(failures)
    test_default_sources_excludes_not_enabled(failures)
    test_source_status_reports_adapter_status(failures)
    test_discover_from_sources_skips_not_enabled_with_zero_calls(failures)
    test_naukri_compatibility_unchanged(failures)
    test_existing_query_snapshot_compatibility(failures)
    test_cross_source_dedup_bangalore_bengaluru(failures)
    test_source_health_record_no_fabricated_data(failures)
    test_future_extensibility_new_not_enabled_adapter_excluded(failures)
    test_discover_from_sources_isolates_unexpected_query_exception(failures)
    test_discover_from_sources_isolates_multiple_unexpected_exceptions(failures)
    test_discover_from_sources_isolates_health_check_exception(failures)
    test_full_regression_suite_files_unaffected(failures)

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll multi-source adapter architecture tests passed.")


if __name__ == "__main__":
    main()
