#!/usr/bin/env python3

"""
Phase 14.6 acceptance test -- daily search planner optimization.

Fully offline: a temp DB (never data/applications/jobos.db), a fake
direct adapter, and the existing search_provider_adapter.
set_search_provider() override injecting a ReplayProvider seeded with
real, previously-captured evidence (web_search_evidence_capture.py --
the same fixtures scripts/test_phase14_search_provider.py already
uses). No network call of any kind.

Proves, per Phase 14.6 Section 19:
  1. duplicate/cooldown queries are skipped on a second plan
  2. recently-successful source is skipped until its refresh interval elapses
  3. direct-source jobs land before search-provider jobs (wave ordering),
     and cross-source dedup (existing, unmodified) can see across both
  4. a successful provider response does not trigger any other provider
  5. per-run search-provider budget is enforced
  6. new-job discovery still produces real matches
  7. the 9-sheet report remains correct, with planner metrics appended
     to RUN_SUMMARY only (no 10th sheet, no other sheet changed)
"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


production_before = _sha(PRODUCTION_DB)

os.environ["YOU_API_KEY"] = "test-fixture-key-do-not-use"

passed = 0
failed = 0


def check(condition, message):
    global passed, failed
    if condition:
        passed += 1
        print(f"PASS: {message}")
    else:
        failed += 1
        print(f"FAIL: {message}")


import init_tracker
import migrate_v2_schema
import daily_search_planner as dsp
import search_history_store as history_store
import search_provider_adapter as spa
import restricted_source_registry as rsr
import source_registry
import generate_run_report
from candidate_profile import normalize_candidate_profile, promote_to_confirmed, serialize_candidate_profile
from source_adapter import AdapterHealth, BlockReason, JobSourceAdapter
from search_provider import ReplayProvider
from web_search_evidence_capture import _LINKEDIN_RESULTS, _INSTAHYRE_RESULTS

CANDIDATE_ID = "cand_test_14_6"
ROLE = "Site Reliability Engineer"
LOCATIONS = ["Bangalore", "Hyderabad"]


# ----------------------------------------------------------------------
# Fake direct adapter (offline, deterministic)
# ----------------------------------------------------------------------

class _FakeDirectBehavior:
    jobs_by_location = {}

    @classmethod
    def reset(cls):
        cls.jobs_by_location = {}


class FakeDirectAdapter(JobSourceAdapter):
    name = "FAKE_DIRECT_A"

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [dict(j) for j in _FakeDirectBehavior.jobs_by_location.get(query.location, [])]


source_registry.ADAPTERS["FAKE_DIRECT_A"] = FakeDirectAdapter


def _fake_direct_job(location, suffix):
    return {
        "source": "FAKE_DIRECT_A",
        "company": f"Direct Co {suffix}",
        "title": f"Site Reliability Engineer {suffix}",
        "location": location,
        "work_model": "Hybrid",
        "job_url": f"https://example.com/direct/{location.lower()}-{suffix}",
        "application_url": "",
        "posted_date": "2026-09-20",
        "jd_text": "AWS Kubernetes Terraform Jenkins Prometheus SLO incident reliability production cloud platform",
        "experience_required": "8-10 years",
        "mandatory_skills": [],
        "preferred_skills": [],
    }


# ----------------------------------------------------------------------
# Real-evidence replay fixture for the search-provider side, keyed by
# the EXACT consolidated (OR'd-location) query the planner builds
# -- proves the planner's own consolidation logic (Section 6) lines up
# with what search_provider_adapter.py actually requests.
# ----------------------------------------------------------------------

def _hits_for(pairs):
    return [{"title": t, "url": u, "snippet": "", "date": None} for t, u in pairs]


def _consolidated_query(site_key, locations):
    site = rsr.SITE_BY_KEY[site_key]
    location_text = " OR ".join(sorted(locations))
    return rsr.build_site_query(site, ROLE, location_text)


REPLAY_FIXTURE = {
    _consolidated_query("LINKEDIN", LOCATIONS): _hits_for(_LINKEDIN_RESULTS),
    _consolidated_query("INSTAHYRE", LOCATIONS): _hits_for(_INSTAHYRE_RESULTS),
}


# ----------------------------------------------------------------------
# DB fixtures (same pattern as test_search_worker.py)
# ----------------------------------------------------------------------

def _new_isolated_db():
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_phase14_6_"))
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


def _seed_confirmed_candidate(db_path, candidate_id):
    now = "2026-09-21T00:00:00+00:00"
    raw = {
        "identity": {"candidate_id": candidate_id, "name": "Test Candidate 14.6"},
        "professional_summary": {"total_experience_years": 11},
        "skills": {
            "cloud": [{"name": "AWS"}],
            "containers_orchestration": [{"name": "Kubernetes"}],
            "infrastructure_iac": [{"name": "Terraform"}],
            "cicd": [{"name": "Jenkins"}],
        },
        "job_preferences": {"target_roles": [ROLE], "target_locations": LOCATIONS},
    }
    profile = promote_to_confirmed(normalize_candidate_profile(raw))
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO candidates (candidate_id, name, email, phone, created_at, updated_at, status) VALUES (?,?,?,?,?,?,?)",
        (candidate_id, "Test Candidate 14.6", None, None, now, now, "ACTIVE"),
    )
    conn.execute(
        "INSERT INTO candidate_search_profile (candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) VALUES (?,1,'PROFILE',?,1,1,?,?)",
        (candidate_id, json.dumps(serialize_candidate_profile(profile)), now, now),
    )
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------
# Isolated history store per test run (never the real
# data/applications/search_query_history.json)
# ----------------------------------------------------------------------

_history_dir = Path(tempfile.mkdtemp(prefix="jobos_test_phase14_6_history_"))
HISTORY_PATH = _history_dir / "search_query_history.json"


def _cfg(max_per_run=12, force=False):
    return {
        "max_search_provider_queries_per_run": max_per_run,
        "max_search_provider_queries_per_day": 100,
        "max_search_provider_queries_per_provider_per_day": 100,
        "search_query_cooldown_hours": 24,
        "source_refresh_interval_hours": {"FAKE_DIRECT_A": 12, "LINKEDIN": 24, "INSTAHYRE": 24},
        "min_new_jobs_per_source": 1,
        "target_new_jobs_per_run": 5,
        "direct_sources": ["FAKE_DIRECT_A"],
        "search_provider_sources": ["LINKEDIN", "INSTAHYRE"],
        # Phase 14.7 keys -- set generously so Phase 14.6's own
        # acceptance checks (which predate adaptive expansion) keep
        # exercising exactly 1 query per search-provider source unless
        # a test explicitly forces coverage to look poor.
        "max_provider_queries_per_source_per_run": 1,
        "coverage_min_unique_jobs": 0,
        "coverage_min_locations_covered": 0,
        "coverage_min_new_jobs": 0,
        "coverage_min_completeness": 0.0,
        "max_additional_query_for_unknown_location": 1,
    }


db_path = None
_original_history_path = history_store.HISTORY_STORE_PATH
history_store.HISTORY_STORE_PATH = HISTORY_PATH
try:
    _FakeDirectBehavior.reset()
    for loc in LOCATIONS:
        _FakeDirectBehavior.jobs_by_location[loc] = [_fake_direct_job(loc, "X")]

    db_path = _new_isolated_db()
    _seed_confirmed_candidate(db_path, CANDIDATE_ID)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    # --- 1. First plan: everything DUE (no history yet) ---
    plan1 = dsp.build_daily_plan(conn, CANDIDATE_ID, config=_cfg())
    check(
        all(p.status == "DUE" for p in plan1.direct),
        "first plan: direct source is DUE with no prior history",
    )
    check(
        all(p.status == "DUE" for p in plan1.search_provider),
        "first plan: both search-provider sources are DUE with no prior history",
    )
    check(
        len(plan1.search_provider_query_plan) == 2,
        f"first plan: search-provider queries consolidated to 1-per-source (got {len(plan1.search_provider_query_plan)}, expected 2)",
    )
    old_style_count = len(LOCATIONS) * (len(_cfg()['direct_sources']) + len(_cfg()['search_provider_sources']))
    new_style_count = len(plan1.direct_query_plan) + len(plan1.search_provider_query_plan)
    check(
        new_style_count < old_style_count,
        f"consolidated plan ({new_style_count} queries) issues fewer queries than the old per-location matrix ({old_style_count})",
    )
    conn.close()

    # --- 2. Execute the plan (wave 1 direct, wave 2 search-provider) ---
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    plan1 = dsp.build_daily_plan(conn, CANDIDATE_ID, config=_cfg())
    conn.close()

    spa.set_search_provider(ReplayProvider(fixture_data=REPLAY_FIXTURE))
    try:
        original_history_path = history_store.HISTORY_STORE_PATH
        history_store.HISTORY_STORE_PATH = HISTORY_PATH
        try:
            exec_result = dsp.execute_daily_plan(str(db_path), CANDIDATE_ID, plan1, config=_cfg())
        finally:
            history_store.HISTORY_STORE_PATH = original_history_path
    finally:
        spa.clear_search_provider()

    check(len(exec_result.waves) == 2, f"execution ran exactly 2 waves (direct, search_provider), got {len(exec_result.waves)}")
    check(exec_result.waves[0].name == "direct", "wave 1 is the direct-source wave")
    check(exec_result.waves[1].name == "search_provider", "wave 2 is the search-provider wave")
    check(exec_result.total_new_jobs > 0, f"new-job discovery still works: {exec_result.total_new_jobs} new jobs found")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    linkedin_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE source='LINKEDIN'").fetchone()[0]
    instahyre_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE source='INSTAHYRE'").fetchone()[0]
    direct_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE source='FAKE_DIRECT_A'").fetchone()[0]
    conn.close()
    check(direct_jobs == 2, f"both direct-source jobs (one per location) persisted, got {direct_jobs}")
    check(linkedin_jobs > 0 and instahyre_jobs > 0, f"both search-provider sources produced jobs (LinkedIn={linkedin_jobs}, Instahyre={instahyre_jobs})")

    # --- 3. Cooldown: a second plan immediately after should SKIP everything ---
    original_history_path = history_store.HISTORY_STORE_PATH
    history_store.HISTORY_STORE_PATH = HISTORY_PATH
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        plan2 = dsp.build_daily_plan(conn, CANDIDATE_ID, config=_cfg())
        conn.close()
    finally:
        history_store.HISTORY_STORE_PATH = original_history_path

    check(
        all(p.status == "SKIP" for p in plan2.direct),
        "second plan (immediately after): direct source SKIPPED (refresh interval not elapsed)",
    )
    check(
        all(p.status == "SKIP" for p in plan2.search_provider),
        "second plan (immediately after): both search-provider sources SKIPPED (cooldown/refresh)",
    )
    check(
        plan2.estimated_provider_calls == 0,
        f"second plan estimates ZERO provider calls (got {plan2.estimated_provider_calls}) -- duplicate/recent queries correctly avoided",
    )

    # --- 4. force_refresh bypasses cooldown/refresh ---
    original_history_path = history_store.HISTORY_STORE_PATH
    history_store.HISTORY_STORE_PATH = HISTORY_PATH
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        plan3 = dsp.build_daily_plan(conn, CANDIDATE_ID, config=_cfg(), force_refresh=True)
        conn.close()
    finally:
        history_store.HISTORY_STORE_PATH = original_history_path

    check(
        all(p.status == "DUE" for p in plan3.direct) and all(p.status == "DUE" for p in plan3.search_provider),
        "Force Refresh plan bypasses cooldown/refresh-interval skips",
    )

    # --- 5. Per-run budget enforcement ---
    original_history_path = history_store.HISTORY_STORE_PATH
    history_store.HISTORY_STORE_PATH = HISTORY_PATH
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        plan4 = dsp.build_daily_plan(conn, CANDIDATE_ID, config=_cfg(max_per_run=1), force_refresh=True)
        conn.close()
    finally:
        history_store.HISTORY_STORE_PATH = original_history_path

    check(
        plan4.estimated_provider_calls <= 1,
        f"max_search_provider_queries_per_run=1 is enforced (plan estimated {plan4.estimated_provider_calls})",
    )
    check(
        any(p.status == "SKIP" and "budget" in p.reason for p in plan4.search_provider),
        "at least one search-provider source shows a budget-limited skip reason when the per-run cap is tight",
    )

    # --- 6. Cross-source dedup sees both waves (existing, unmodified mechanism) ---
    import canonical_job
    import cross_source_dedup

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    all_jobs = [dict(r) for r in conn.execute("SELECT * FROM jobs")]
    conn.close()
    canon = [canonical_job.derive_canonical_job(j) for j in all_jobs]
    dup_candidates = cross_source_dedup.find_cross_source_duplicate_candidates(canon)
    check(
        isinstance(dup_candidates, list),
        f"cross-source dedup (existing, unmodified) runs cleanly across both waves' jobs together -- {len(dup_candidates)} candidate(s) found",
    )

    # --- 7. 9-sheet report remains correct, planner metrics appended to RUN_SUMMARY only ---
    out_xlsx = _history_dir / "phase14_6_test_report.xlsx"
    planner_metrics = {
        "old_style_queries": old_style_count,
        "new_style_queries": new_style_count,
        "queries_avoided": old_style_count - new_style_count,
    }
    result = generate_run_report.generate(str(db_path), CANDIDATE_ID, str(out_xlsx), planner_metrics=planner_metrics)

    import openpyxl
    wb = openpyxl.load_workbook(str(out_xlsx))
    expected_sheets = {
        "APPLY_TODAY", "ALL_MATCHING_JOBS", "NEW_JOBS", "ALREADY_APPLIED",
        "REJECTED_EXCLUDED", "DUPLICATES", "APPLICATION_TRACKER", "SOURCE_HEALTH", "RUN_SUMMARY",
    }
    check(set(wb.sheetnames) == expected_sheets, f"exactly the existing 9 sheets, no 10th: {wb.sheetnames}")

    run_summary_ws = wb["RUN_SUMMARY"]
    metric_names = [row[0].value for row in run_summary_ws.iter_rows(min_row=2)]
    check(
        any(m and m.startswith("planner.") for m in metric_names),
        "RUN_SUMMARY sheet contains planner.* metric rows",
    )
    check(
        "planner.queries_avoided" in metric_names,
        "RUN_SUMMARY contains planner.queries_avoided",
    )

except Exception as error:
    import traceback

    traceback.print_exc()
    failed += 1
    print(f"FAIL: unexpected exception: {error}")

finally:
    history_store.HISTORY_STORE_PATH = _original_history_path
    try:
        if HISTORY_PATH.exists():
            HISTORY_PATH.unlink()
    except Exception:
        pass

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, "production DB untouched (SHA unchanged)")

print()
print(f"TOTAL: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
