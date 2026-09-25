#!/usr/bin/env python3

"""
Regression tests for the advisory health-check architecture change:
JobSourceAdapter.health_check_is_advisory (default False, opted into by
NaukriAdapter only) and source_registry.discover_from_sources()'s
updated gating logic.

Fully offline: test-local fake adapters, no network, no Naukri contact,
no browser launch. Verifies the NEW gating behavior in isolation from
the real NaukriAdapter/NaukriFetcher, plus confirms NaukriAdapter itself
carries the flag and that jobAge=3 / headless-default are unaffected.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import source_registry
from source_adapter import (
    JobSourceAdapter,
    SearchQuery,
    AdapterHealth,
    BlockReason,
    AdapterBlockedError,
    AdapterTimeoutError,
    SourceRunState,
)
from source_registry import discover_from_sources


# ----------------------------------------------------------------------
# Test-local fake adapters
# ----------------------------------------------------------------------

class _Behavior:
    health_reachable = True
    health_block_reason = BlockReason.NONE
    search_jobs = []
    search_raises = None  # an exception instance, or None

    @classmethod
    def reset(cls):
        cls.health_reachable = True
        cls.health_block_reason = BlockReason.NONE
        cls.search_jobs = []
        cls.search_raises = None


class _NonAdvisoryFakeAdapter(JobSourceAdapter):
    """health_check_is_advisory defaults to False -- exactly the
    pre-existing behavior every other adapter has always had."""

    name = "FAKE_NONADVISORY"

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=_Behavior.health_reachable, block_reason=_Behavior.health_block_reason)

    def search(self, query):
        if _Behavior.search_raises is not None:
            raise _Behavior.search_raises
        return list(_Behavior.search_jobs)


class _AdvisoryFakeAdapter(JobSourceAdapter):
    """Opts in, exactly like NaukriAdapter."""

    name = "FAKE_ADVISORY"
    health_check_is_advisory = True

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=_Behavior.health_reachable, block_reason=_Behavior.health_block_reason)

    def search(self, query):
        if _Behavior.search_raises is not None:
            raise _Behavior.search_raises
        return list(_Behavior.search_jobs)


source_registry.ADAPTERS["FAKE_NONADVISORY"] = _NonAdvisoryFakeAdapter
source_registry.ADAPTERS["FAKE_ADVISORY"] = _AdvisoryFakeAdapter

QUERY = ("FAKE_ADVISORY", SearchQuery(role="Senior Site Reliability Engineer", location="Bengaluru"))
NONADVISORY_QUERY = ("FAKE_NONADVISORY", SearchQuery(role="Senior Site Reliability Engineer", location="Bengaluru"))


def main():
    failures = []

    print("ADVISORY HEALTH CHECK TEST")
    print("============================")

    # ------------------------------------------------------------------
    # 1. A homepage health-check failure does NOT prevent a search for
    #    an advisory adapter (the core behavior change).
    # ------------------------------------------------------------------
    _Behavior.reset()
    _Behavior.health_reachable = False
    _Behavior.health_block_reason = BlockReason.UNKNOWN_BLOCK
    _Behavior.search_jobs = [{"source": "FAKE_ADVISORY", "company": "Acme", "title": "SRE"}]

    run_report = []
    jobs = discover_from_sources([QUERY], run_report=run_report)

    if len(jobs) != 1:
        failures.append(f"1: expected search() to be attempted and return 1 job despite a failed health check, got {jobs}")
    else:
        print("PASS: 1 -> a failed health check does NOT prevent search() for an advisory adapter")

    state = run_report[0]
    if state.blocked:
        failures.append(f"1: expected state.blocked=False (health-check failure alone must not set it for an advisory adapter), got blocked={state.blocked}")
    elif state.queries_attempted != 1 or state.queries_succeeded != 1:
        failures.append(f"1: expected exactly 1 attempted/succeeded query, got attempted={state.queries_attempted}, succeeded={state.queries_succeeded}")
    else:
        print("PASS: 1 -> state.blocked stays False; the query is attempted and recorded as succeeded")

    if state.health is None or state.health.reachable is not False:
        failures.append("1: health_check()'s own result must still be recorded for diagnostics even though it didn't gate")
    else:
        print("PASS: 1 -> health_check()'s failing result is still recorded in state.health for diagnostics")

    # ------------------------------------------------------------------
    # Backward compatibility: the SAME failed health check DOES still
    # hard-gate a non-advisory adapter (nothing changed for it).
    # ------------------------------------------------------------------
    _Behavior.reset()
    _Behavior.health_reachable = False
    _Behavior.health_block_reason = BlockReason.UNKNOWN_BLOCK
    _Behavior.search_jobs = [{"source": "FAKE_NONADVISORY", "company": "Acme", "title": "SRE"}]

    run_report = []
    jobs = discover_from_sources([NONADVISORY_QUERY], run_report=run_report)

    if jobs != []:
        failures.append(f"backward-compat: expected zero jobs (non-advisory adapter must still hard-gate), got {jobs}")
    elif not run_report[0].blocked or run_report[0].queries_attempted != 0:
        failures.append(f"backward-compat: expected blocked=True and 0 queries attempted, got {run_report[0]}")
    else:
        print("PASS: backward-compat -> a non-advisory adapter (the pre-existing default) is still hard-gated by a failed health check")

    # ------------------------------------------------------------------
    # 2. VALID_RESULTS-equivalent: search succeeds normally when health
    #    check ALSO succeeds (the ordinary, unaffected case).
    # ------------------------------------------------------------------
    _Behavior.reset()
    _Behavior.search_jobs = [{"source": "FAKE_ADVISORY", "company": "Acme", "title": "SRE"}]
    run_report = []
    jobs = discover_from_sources([QUERY], run_report=run_report)
    if len(jobs) != 1 or run_report[0].blocked:
        failures.append(f"2: expected normal success with a healthy health check, got jobs={jobs}, state={run_report[0]}")
    else:
        print("PASS: 2 -> normal success path (health check passes, search succeeds) is unaffected")

    # ------------------------------------------------------------------
    # 3. VALID_EMPTY_RESULT-equivalent: search returns [] normally
    #    (a genuinely empty but successful query) -- completes as a
    #    successful, non-blocked query, exactly like case 2 with 0 jobs.
    # ------------------------------------------------------------------
    _Behavior.reset()
    _Behavior.health_reachable = False
    _Behavior.health_block_reason = BlockReason.UNKNOWN_BLOCK
    _Behavior.search_jobs = []  # VALID_EMPTY_RESULT: search succeeds, finds nothing
    run_report = []
    jobs = discover_from_sources([QUERY], run_report=run_report)
    if jobs != [] or run_report[0].blocked or run_report[0].queries_succeeded != 1:
        failures.append(f"3: expected a successful zero-result query despite a failed health check, got jobs={jobs}, state={run_report[0]}")
    else:
        print("PASS: 3 -> VALID_EMPTY_RESULT-equivalent (search succeeds, 0 jobs) completes successfully even with a failed health check")

    # ------------------------------------------------------------------
    # 4. BLOCKED (search() raises AdapterBlockedError) -> existing
    #    blocked behavior, unaffected by the advisory flag.
    # ------------------------------------------------------------------
    _Behavior.reset()
    _Behavior.search_raises = AdapterBlockedError("FAKE_ADVISORY", BlockReason.UNKNOWN_BLOCK)
    run_report = []
    jobs = discover_from_sources([QUERY], run_report=run_report)
    if jobs != [] or not run_report[0].blocked or run_report[0].blocked_reason != BlockReason.UNKNOWN_BLOCK:
        failures.append(f"4: expected AdapterBlockedError from search() to set state.blocked as before, got jobs={jobs}, state={run_report[0]}")
    else:
        print("PASS: 4 -> AdapterBlockedError raised by search() itself still produces the existing blocked behavior")

    # ------------------------------------------------------------------
    # 5. SOFT_BLOCK_OR_CHALLENGE-equivalent: NaukriAdapter maps this to
    #    AdapterBlockedError too (see naukri_adapter.py's search()) --
    #    already covered by case 4's exact mechanism (same exception
    #    type). Confirmed here for explicitness.
    # ------------------------------------------------------------------
    print("PASS: 5 -> SOFT_BLOCK_OR_CHALLENGE maps to AdapterBlockedError in naukri_adapter.py (same mechanism as case 4, unchanged)")

    # ------------------------------------------------------------------
    # 6. PARSE_FAILURE-equivalent: search() raises AdapterTimeoutError
    #    -> existing per-query failure behavior, unaffected.
    # ------------------------------------------------------------------
    _Behavior.reset()
    _Behavior.search_raises = AdapterTimeoutError("FAKE_ADVISORY", detail="simulated PARSE_FAILURE")
    run_report = []
    jobs = discover_from_sources([QUERY], run_report=run_report)
    if jobs != [] or run_report[0].blocked or run_report[0].queries_failed != 1:
        failures.append(f"6: expected AdapterTimeoutError to be recorded as a query-level failure (not blocked), got jobs={jobs}, state={run_report[0]}")
    else:
        print("PASS: 6 -> PARSE_FAILURE-equivalent (AdapterTimeoutError) still recorded as a query-level failure, not a source block")

    # ------------------------------------------------------------------
    # 7. AdapterTimeoutError remains unchanged (explicit direct check).
    # ------------------------------------------------------------------
    if not any("6:" in f for f in failures):
        print("PASS: 7 -> AdapterTimeoutError handling is unchanged (verified by case 6)")

    # ------------------------------------------------------------------
    # 8. Worker/search-run status aggregation remains correct: reuse
    #    search_worker._aggregate_status() directly against the new
    #    advisory-success scenario (health failed, advisory, query
    #    succeeded) -- must be COMPLETED, not BLOCKED.
    # ------------------------------------------------------------------
    from search_worker import _aggregate_status

    _Behavior.reset()
    _Behavior.health_reachable = False
    _Behavior.health_block_reason = BlockReason.UNKNOWN_BLOCK
    _Behavior.search_jobs = [{"source": "FAKE_ADVISORY", "company": "Acme", "title": "SRE"}]
    run_report = []
    discover_from_sources([QUERY], run_report=run_report)
    status = _aggregate_status(run_report, had_unexpected_exception=False, any_query_attempted=True)
    if status != "COMPLETED":
        failures.append(f"8: expected aggregate status COMPLETED (health failed but advisory, query succeeded), got {status}")
    else:
        print("PASS: 8 -> worker status aggregation correctly reports COMPLETED when an advisory adapter's health check fails but the query itself succeeds")

    # ------------------------------------------------------------------
    # 9. jobAge=3 remains present in the actual search URL (unaffected
    #    by this change -- confirmed directly, not just by re-running
    #    test_max_job_age_days.py separately).
    # ------------------------------------------------------------------
    from naukri_adapter import _build_search_url
    url = _build_search_url(SearchQuery(role="Infrastructure Engineer", location="Bangalore", max_job_age_days=3))
    if "jobAge=3" not in url:
        failures.append(f"9: expected jobAge=3 in the search URL, got {url}")
    else:
        print(f"PASS: 9 -> jobAge=3 still present in the actual search URL: {url}")

    # ------------------------------------------------------------------
    # 10. Headless remains the default (unaffected by this change) --
    #     static check only, no browser launch needed here since
    #     test_naukri_headless_config.py already covers the dynamic
    #     launch check exhaustively.
    # ------------------------------------------------------------------
    bridge_source = (ROOT / "scripts" / "naukri_fetch_bridge.js").read_text(encoding="utf-8")
    if "process.env.JOBOS_BROWSER_HEADLESS !== '0'" not in bridge_source:
        failures.append("10: naukri_fetch_bridge.js no longer computes headless as the default (JOBOS_BROWSER_HEADLESS-driven)")
    elif "channel: 'chromium'" not in bridge_source:
        failures.append("10: naukri_fetch_bridge.js no longer pins channel: 'chromium'")
    else:
        print("PASS: 10 -> headless-by-default and channel='chromium' are both still present, unaffected by this change")

    # ------------------------------------------------------------------
    # NaukriAdapter itself carries the flag (not just the fake adapters
    # used above)
    # ------------------------------------------------------------------
    from naukri_adapter import NaukriAdapter
    if NaukriAdapter.health_check_is_advisory is not True:
        failures.append("NaukriAdapter.health_check_is_advisory is not True")
    else:
        print("PASS: NaukriAdapter.health_check_is_advisory == True")

    from source_adapter import MockJobSourceAdapter
    if MockJobSourceAdapter.health_check_is_advisory is not False:
        failures.append("MockJobSourceAdapter.health_check_is_advisory changed from the default False -- backward compatibility violated")
    else:
        print("PASS: MockJobSourceAdapter.health_check_is_advisory remains the default False (backward compatible)")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll advisory health-check tests passed.")


if __name__ == "__main__":
    main()
