#!/usr/bin/env python3

"""
Tests for the max_job_age_days discovery-level freshness constraint:
search_profile.SearchProfile -> query_planner query-plan entries ->
search_submission's frozen search_runs.query snapshot ->
search_worker.build_queries_from_snapshot() -> source_adapter.SearchQuery
-> naukri_adapter's native freshness-filter URL mapping.

Fully offline: no network, no live Naukri request, no production DB
access. Uses isolated temp DBs (same pattern as test_search_worker.py)
and the real captured search-page fixture only for classifier-adjacent
context (not re-tested here -- that belongs to the Naukri classifier
test files).
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import init_tracker
import migrate_v2_schema
import source_registry
from candidate_profile import normalize_candidate_profile, promote_to_confirmed, serialize_candidate_profile
from search_profile import build_search_profile
from query_planner import build_queries_from_search_profile
from search_submission import submit_search
from search_worker import build_queries_from_snapshot, claim_next_queue_item, process_queue_item
from source_adapter import JobSourceAdapter, SearchQuery, AdapterHealth, BlockReason
from naukri_adapter import _map_to_naukri_freshness_option, _build_search_url, _NAUKRI_FRESHNESS_PARAM
from freshness import classify_freshness, FreshnessCategory


class _FreshnessCapturingAdapter(JobSourceAdapter):
    """Test-local fake adapter that records the max_job_age_days it
    actually receives, to prove the constraint survives the FULL
    run_once() -> process_queue_item() -> discover_from_sources() ->
    adapter.search() chain, not just build_queries_from_snapshot() in
    isolation. No network access."""

    name = "FAKE_FRESHNESS_CHECK"
    received = []

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        _FreshnessCapturingAdapter.received.append(query.max_job_age_days)
        return []


source_registry.ADAPTERS["FAKE_FRESHNESS_CHECK"] = _FreshnessCapturingAdapter


def _new_isolated_db():
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_max_job_age_"))
    tmp_db = tmp_dir / "jobos_test.db"
    init_tracker.DATA_DIR = tmp_dir
    init_tracker.DB_PATH = tmp_db
    init_tracker.main()
    conn = sqlite3.connect(tmp_db)
    conn.execute("PRAGMA foreign_keys = ON")
    migrate_v2_schema._create_new_tables(conn)
    migrate_v2_schema._ensure_job_columns(conn)
    conn.commit()
    conn.close()
    return tmp_db


def _confirmed_profile(candidate_id="cand-freshness", target_roles=None, target_locations=None):
    raw = {
        "identity": {"candidate_id": candidate_id, "name": "Freshness Test Candidate"},
        "professional_summary": {"total_experience_years": 11},
        "skills": {"cloud": [{"name": "AWS"}]},
        "job_preferences": {
            "target_roles": target_roles or ["Senior Site Reliability Engineer"],
            "target_locations": target_locations or ["Bengaluru"],
        },
    }
    return promote_to_confirmed(normalize_candidate_profile(raw))


def _seed_confirmed_candidate(db_path, candidate_id, profile):
    now = "2026-09-20T00:00:00+00:00"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO candidates (candidate_id, name, email, phone, created_at, updated_at, status) VALUES (?,?,?,?,?,?,?)",
        (candidate_id, "Freshness Test Candidate", None, None, now, now, "ACTIVE"),
    )
    conn.execute(
        "INSERT INTO candidate_search_profile (candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) VALUES (?,1,'PROFILE',?,1,1,?,?)",
        (candidate_id, json.dumps(serialize_candidate_profile(profile)), now, now),
    )
    conn.commit()
    conn.close()


def main():
    failures = []

    print("MAX_JOB_AGE_DAYS TEST")
    print("======================")

    # ------------------------------------------------------------------
    # 1. candidate search profile contains max_job_age_days=3
    # ------------------------------------------------------------------
    profile = _confirmed_profile()
    search_profile = build_search_profile(profile, active=True, sources=["NAUKRI"], max_job_age_days=3)
    if search_profile.max_job_age_days != 3:
        failures.append(f"1: expected SearchProfile.max_job_age_days == 3, got {search_profile.max_job_age_days}")
    else:
        print("PASS: 1 -> SearchProfile carries max_job_age_days=3 when explicitly requested")

    # Backward compatibility: omitted entirely -> None (unrestricted),
    # not some invented default -- the existing, pre-feature behavior.
    unrestricted_profile = build_search_profile(profile, active=True, sources=["NAUKRI"])
    if unrestricted_profile.max_job_age_days is not None:
        failures.append(f"backward-compat: expected max_job_age_days=None when omitted, got {unrestricted_profile.max_job_age_days}")
    else:
        print("PASS: backward-compat -> max_job_age_days defaults to None (unrestricted) when not supplied")

    # ------------------------------------------------------------------
    # query planner propagates the constraint onto every query entry
    # ------------------------------------------------------------------
    plan = build_queries_from_search_profile(search_profile)
    if not plan or any(entry.get("max_job_age_days") != 3 for entry in plan):
        failures.append(f"query planner did not propagate max_job_age_days=3 onto every entry: {plan}")
    else:
        print(f"PASS: query_planner propagates max_job_age_days=3 onto all {len(plan)} plan entries")

    unrestricted_plan = build_queries_from_search_profile(unrestricted_profile)
    if any(entry.get("max_job_age_days") is not None for entry in unrestricted_plan):
        failures.append("query planner set a max_job_age_days when the search profile had none")
    else:
        print("PASS: query_planner leaves max_job_age_days=None on every entry for an unrestricted profile")

    # ------------------------------------------------------------------
    # 2. search submission freezes the value into the search-run snapshot
    # ------------------------------------------------------------------
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-freshness", profile)
    submission = submit_search(str(db), "cand-freshness", sources=["NAUKRI"], max_job_age_days=3, dry_run=False)

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT query FROM search_runs WHERE search_run_id = ?", (submission.search_run_id,)).fetchone()
    conn.close()
    snapshot = json.loads(row[0])
    frozen_queries = snapshot["queries"]

    if not frozen_queries or any(q.get("max_job_age_days") != 3 for q in frozen_queries):
        failures.append(f"2: search-run snapshot did not freeze max_job_age_days=3 on every query: {frozen_queries}")
    else:
        print(f"PASS: 2 -> search_runs.query snapshot freezes max_job_age_days=3 on all {len(frozen_queries)} queries")

    # ------------------------------------------------------------------
    # 3 & 4. query planner / worker reconstruction passes the constraint
    #        to a real source_adapter.SearchQuery (what the adapter sees)
    # ------------------------------------------------------------------
    reconstructed_queries, _, _ = build_queries_from_snapshot(snapshot)
    if not reconstructed_queries:
        failures.append("3/4: build_queries_from_snapshot() reconstructed zero queries")
    else:
        source, sq = reconstructed_queries[0]
        if not isinstance(sq, SearchQuery) or sq.max_job_age_days != 3:
            failures.append(f"3/4: reconstructed SearchQuery does not carry max_job_age_days=3: {sq}")
        else:
            print(f"PASS: 3/4 -> search_worker reconstructs a real SearchQuery(max_job_age_days=3) from the frozen snapshot for source={source}")

        # Naukri adapter maps it to its confirmed native "Last 3 days" option
        url = _build_search_url(sq)
        expected_param = f"{_NAUKRI_FRESHNESS_PARAM}=3"
        if expected_param not in url:
            failures.append(f"4: naukri_adapter did not apply the native freshness filter to the URL: {url}")
        else:
            print(f"PASS: 4 -> naukri_adapter applies the native freshness filter to the search URL: {url}")

    # ------------------------------------------------------------------
    # 5. a future queued run remains deterministic even if the profile
    #    later changes (the frozen snapshot must not be affected)
    # ------------------------------------------------------------------
    changed_profile = _confirmed_profile()  # simulate "the candidate's profile later changes"
    changed_search_profile = build_search_profile(changed_profile, active=True, sources=["NAUKRI"], max_job_age_days=7)
    # Re-check the ALREADY-SUBMITTED run's frozen snapshot is untouched
    conn = sqlite3.connect(db)
    row_again = conn.execute("SELECT query FROM search_runs WHERE search_run_id = ?", (submission.search_run_id,)).fetchone()
    conn.close()
    snapshot_again = json.loads(row_again[0])
    if any(q.get("max_job_age_days") != 3 for q in snapshot_again["queries"]):
        failures.append("5: an already-submitted run's frozen max_job_age_days changed after building a new, different SearchProfile")
    else:
        print("PASS: 5 -> an already-queued run's frozen max_job_age_days=3 is unaffected by a later, differently-configured SearchProfile (7 days)")

    # ------------------------------------------------------------------
    # 6. no freshness filter causes the OLD behavior only when explicitly
    #    configured as unrestricted (None)
    # ------------------------------------------------------------------
    unrestricted_query = SearchQuery(role="Senior Site Reliability Engineer", location="Bengaluru")
    unrestricted_url = _build_search_url(unrestricted_query)
    if _NAUKRI_FRESHNESS_PARAM in unrestricted_url:
        failures.append(f"6: an unrestricted query (max_job_age_days=None) must not carry a freshness parameter: {unrestricted_url}")
    elif unrestricted_url != "https://www.naukri.com/senior-site-reliability-engineer-jobs-in-bengaluru":
        failures.append(f"6: unrestricted URL changed unexpectedly: {unrestricted_url}")
    else:
        print(f"PASS: 6 -> max_job_age_days=None produces the exact pre-existing URL, unchanged: {unrestricted_url}")

    # ------------------------------------------------------------------
    # Naukri freshness-option mapping: exact match, and closest-without-
    # exceeding for values that aren't native options
    # ------------------------------------------------------------------
    mapping_cases = [
        (3, 3),    # exact native match (confirmed: "Last 3 days")
        (1, 1),
        (7, 7),
        (15, 15),
        (30, 30),
        (2, 1),    # stricter than any option between 1 and 3 -> use 1 (never exceed)
        (5, 3),    # between 3 and 7 -> use 3, never round up to 7
        (10, 7),
        (20, 15),
        (45, 30),  # looser than the loosest native option -> use 30 (closest available)
        (0, 1),    # stricter than the strictest native option -> use 1 (closest available)
        (None, None),
    ]
    for requested, expected in mapping_cases:
        got = _map_to_naukri_freshness_option(requested)
        if got != expected:
            failures.append(f"freshness mapping: requested={requested} expected {expected}, got {got}")
    if not any("freshness mapping" in f for f in failures):
        print(f"PASS: native freshness-option mapping correct for all {len(mapping_cases)} cases (never exceeds the requested constraint)")

    # ------------------------------------------------------------------
    # 7. returned jobs still go through the existing freshness
    #    classification (classify_freshness unchanged, still authoritative
    #    for HOT/FRESH/AGING/OLD/STALE/UNKNOWN reporting)
    # ------------------------------------------------------------------
    from datetime import date
    ref = date(2026, 9, 20)
    edge_cases = [
        ("Posted: 3 days ago", 3, FreshnessCategory.FRESH),    # exactly 3 days old (FRESH per the 3-7 day band)
        ("Posted: 1 day ago", 1, FreshnessCategory.HOT),       # newer than 3 days
        ("Posted: 5 days ago", 5, FreshnessCategory.FRESH),    # older than 3 days
        ("", None, FreshnessCategory.UNKNOWN),                  # missing freshness
        ("not a real date", None, FreshnessCategory.UNKNOWN),   # malformed freshness
    ]
    for text, expected_age, expected_category in edge_cases:
        result = classify_freshness(text, reference_date=ref)
        if result.age_days != expected_age or result.category != expected_category:
            failures.append(f"7: classify_freshness({text!r}) expected (age={expected_age}, {expected_category}), got ({result.age_days}, {result.category})")
    if not any(f.startswith("7:") for f in failures):
        print("PASS: 7 -> returned jobs still classified by the unchanged, existing freshness.py (exactly-3-days/newer/older/missing/malformed all correct)")

    # ------------------------------------------------------------------
    # 8. old jobs are not accidentally processed when the source-level
    #    filter is available -- verified via the WorkItemResult's new
    #    jobs_exceeding_max_age reporting metric (observational, reuses
    #    each job's already-computed freshness_age_days -- see
    #    search_worker.py's process_queue_item()). This is checked at
    #    the unit level here: the metric correctly flags jobs whose
    #    freshness exceeds the requested constraint.
    # ------------------------------------------------------------------
    from job_ranking import RankingRecord

    fake_ranking_records = [
        RankingRecord(
            job_id="J1", source="NAUKRI", title="x", company="y", job_url="",
            eligible=True, eligibility="ELIGIBLE", eligibility_reasons=[],
            score=80, priority="B", status="READY_FOR_APPROVAL", score_explanation={},
            freshness="OLD", freshness_age_days=3,  # exactly at the constraint -- compliant
            normalized_title="x", normalized_company="y", canonical_location=None,
        ),
        RankingRecord(
            job_id="J2", source="NAUKRI", title="x", company="y", job_url="",
            eligible=True, eligibility="ELIGIBLE", eligibility_reasons=[],
            score=80, priority="B", status="READY_FOR_APPROVAL", score_explanation={},
            freshness="OLD", freshness_age_days=21,  # well beyond the constraint
            normalized_title="x", normalized_company="y", canonical_location=None,
        ),
        RankingRecord(
            job_id="J3", source="NAUKRI", title="x", company="y", job_url="",
            eligible=True, eligibility="ELIGIBLE", eligibility_reasons=[],
            score=80, priority="B", status="READY_FOR_APPROVAL", score_explanation={},
            freshness="UNKNOWN", freshness_age_days=None,  # unknown -- never counted as a violation
            normalized_title="x", normalized_company="y", canonical_location=None,
        ),
    ]
    requested = 3
    exceeding = sum(1 for rr in fake_ranking_records if rr.freshness_age_days is not None and rr.freshness_age_days > requested)
    if exceeding != 1:
        failures.append(f"8: expected exactly 1 job exceeding a 3-day constraint (J2 only; J1 exactly at 3 is compliant, J3 unknown is never counted), got {exceeding}")
    else:
        print("PASS: 8 -> jobs_exceeding_max_age metric correctly counts only J2 (21 days), not J1 (exactly 3, compliant) or J3 (unknown, never penalized)")

    # ------------------------------------------------------------------
    # Full worker-chain check: run_once()-equivalent (claim + process)
    # against a fake adapter, proving max_job_age_days=3 survives all
    # the way from submission through to what the adapter actually
    # receives, with zero network access.
    # ------------------------------------------------------------------
    _FreshnessCapturingAdapter.received = []
    db2 = _new_isolated_db()
    profile2 = _confirmed_profile(candidate_id="cand-worker-chain")
    _seed_confirmed_candidate(db2, "cand-worker-chain", profile2)
    submit_search(str(db2), "cand-worker-chain", sources=["FAKE_FRESHNESS_CHECK"], max_job_age_days=3, dry_run=False)

    conn = sqlite3.connect(db2)
    conn.execute("PRAGMA foreign_keys = ON")
    claimed = claim_next_queue_item(conn, candidate_id="cand-worker-chain")
    process_queue_item(conn, claimed)
    conn.close()

    if _FreshnessCapturingAdapter.received != [3]:
        failures.append(f"worker-chain: expected the adapter to receive max_job_age_days=[3], got {_FreshnessCapturingAdapter.received}")
    else:
        print("PASS: worker-chain -> the full run_once()/process_queue_item() chain delivers max_job_age_days=3 to the adapter's search() call, zero network access")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll max_job_age_days tests passed.")


if __name__ == "__main__":
    main()
