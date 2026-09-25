#!/usr/bin/env python3

"""
Phase 14.8 acceptance test -- honest location-coverage semantics.

Fully offline: a fresh temp DB and a fresh, isolated
search_history_store.json per scenario, a fake direct adapter, and the
existing search_provider_adapter.set_search_provider() override
injecting a ReplayProvider keyed by the EXACT query text
build_site_query() would build. No network call of any kind. Reuses
the same real-evidence-shaped fixture pattern as
test_phase14_7_adaptive_expansion.py.

Proves (Section 8):
  - known locations count correctly
  - unknown locations do not get guessed
  - unknown metadata does not cause runaway query expansion
  - query budgets still work
  - cooldown still works
  - adaptive queries still target genuinely uncovered locations when
    evidence supports doing so
"""

import hashlib
import json
import os
import re
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
from candidate_profile import normalize_candidate_profile, promote_to_confirmed, serialize_candidate_profile
from source_adapter import AdapterHealth, BlockReason, JobSourceAdapter
from search_provider import ReplayProvider

ROLE = "Site Reliability Engineer"
ALL_LOCATIONS = ["Bangalore", "Hyderabad", "Pune", "Chennai"]
SITE_KEY = "LINKEDIN"


def _new_isolated_db():
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_phase14_8_"))
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
    return tmp_db, tmp_dir


def _seed_confirmed_candidate(db_path, candidate_id, locations=None):
    now = "2026-09-23T00:00:00+00:00"
    raw = {
        "identity": {"candidate_id": candidate_id, "name": "Test Candidate 14.8"},
        "professional_summary": {"total_experience_years": 11},
        "skills": {"cloud": [{"name": "AWS"}]},
        "job_preferences": {"target_roles": [ROLE], "target_locations": locations or ALL_LOCATIONS},
    }
    profile = promote_to_confirmed(normalize_candidate_profile(raw))
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO candidates (candidate_id, name, email, phone, created_at, updated_at, status) VALUES (?,?,?,?,?,?,?)",
        (candidate_id, "Test Candidate 14.8", None, None, now, now, "ACTIVE"),
    )
    conn.execute(
        "INSERT INTO candidate_search_profile (candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) VALUES (?,1,'PROFILE',?,1,1,?,?)",
        (candidate_id, json.dumps(serialize_candidate_profile(profile)), now, now),
    )
    conn.commit()
    conn.close()


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


def _hit_known(idx, city_slug, company="Acme"):
    """A hit whose title carries a genuinely parseable city -- KNOWN."""
    return {
        "title": f"{company} hiring Site Reliability Engineer in {city_slug}, India | LinkedIn",
        "url": f"https://in.linkedin.com/jobs/view/site-reliability-engineer-at-{company.lower()}-{city_slug.lower()}-{10000000 + idx}",
        "snippet": "", "date": None,
    }


def _hit_unknown(idx, company="Globex"):
    """A hit whose company/title DO parse (so normalization doesn't
    reject it for a missing required field) but whose location text
    ("Karnataka", a state, not a city) location_taxonomy.py correctly
    classifies as UNKNOWN -- never guessed into a specific configured
    city."""
    return {
        "title": f"{company} hiring Site Reliability Engineer in Karnataka | LinkedIn",
        "url": f"https://in.linkedin.com/jobs/view/site-reliability-engineer-at-{company.lower()}-{20000000 + idx}",
        "snippet": "", "date": None,
    }


def _query_text(locations):
    site = rsr.SITE_BY_KEY[SITE_KEY]
    location_text = " OR ".join(sorted(locations)) if len(locations) > 1 else locations[0]
    return rsr.build_site_query(site, ROLE, location_text)


def _cfg(**overrides):
    cfg = {
        "max_search_provider_queries_per_run": 12,
        "max_search_provider_queries_per_day": 100,
        "max_search_provider_queries_per_provider_per_day": 100,
        "search_query_cooldown_hours": 24,
        "source_refresh_interval_hours": {"FAKE_DIRECT_A": 12, "LINKEDIN": 24},
        "min_new_jobs_per_source": 1,
        "target_new_jobs_per_run": 5,
        "direct_sources": ["FAKE_DIRECT_A"],
        "search_provider_sources": ["LINKEDIN"],
        "max_provider_queries_per_source_per_run": 2,
        "coverage_min_unique_jobs": 5,
        "coverage_min_locations_covered": 3,
        "coverage_min_new_jobs": 3,
        "coverage_min_completeness": 0.0,
        "max_additional_query_for_unknown_location": 1,
    }
    cfg.update(overrides)
    return cfg


def _run_scenario(name, fixture, locations=ALL_LOCATIONS, cfg_overrides=None):
    db_path, tmp_dir = _new_isolated_db()
    candidate_id = f"cand_test_14_8_{name}"
    _seed_confirmed_candidate(db_path, candidate_id, locations=locations)

    history_path = tmp_dir / "history.json"
    original_history_path = history_store.HISTORY_STORE_PATH
    history_store.HISTORY_STORE_PATH = history_path

    _FakeDirectBehavior.reset()

    cfg = _cfg(**(cfg_overrides or {}))

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        plan = dsp.build_daily_plan(conn, candidate_id, target_roles_override=[ROLE], target_locations_override=locations, config=cfg)
        conn.close()

        spa.set_search_provider(ReplayProvider(fixture_data=fixture))
        try:
            exec_result = dsp.execute_daily_plan(db_path, candidate_id, plan, target_roles_override=[ROLE], target_locations_override=locations, config=cfg)
        finally:
            spa.clear_search_provider()

        return db_path, candidate_id, cfg, plan, exec_result
    finally:
        history_store.HISTORY_STORE_PATH = original_history_path


try:
    # ------------------------------------------------------------------
    # 1. Known locations count correctly
    # ------------------------------------------------------------------
    fixture_known = {
        _query_text(ALL_LOCATIONS): (
            [_hit_known(i, "Bangalore") for i in range(3)]
            + [_hit_known(10 + i, "Hyderabad") for i in range(2)]
            + [_hit_known(20 + i, "Pune") for i in range(2)]
            + [_hit_known(30 + i, "Chennai") for i in range(2)]
        )
    }
    _, _, _, _, res_known = _run_scenario("known", fixture_known)
    cov_known = res_known.coverage["LINKEDIN"]
    check(cov_known["known_location_jobs"] == 9, f"1: 9 jobs with parseable locations correctly counted as known_location_jobs (got {cov_known['known_location_jobs']})")
    check(cov_known["unknown_location_jobs"] == 0, f"1: 0 unknown_location_jobs when every hit has a parseable city (got {cov_known['unknown_location_jobs']})")
    check(set(cov_known["covered_cities"]) == {"Bengaluru", "Hyderabad", "Pune", "Chennai"}, f"1: all 4 real cities correctly counted as covered, got {cov_known['covered_cities']}")

    # ------------------------------------------------------------------
    # 2. Unknown locations do not get guessed
    # ------------------------------------------------------------------
    fixture_unknown = {
        _query_text(ALL_LOCATIONS): [_hit_unknown(i) for i in range(6)],
    }
    _, _, _, _, res_unknown = _run_scenario("unknown", fixture_unknown, cfg_overrides={"max_additional_query_for_unknown_location": 0})
    cov_unknown = res_unknown.coverage["LINKEDIN"]
    check(cov_unknown["unknown_location_jobs"] >= 6, f"2: hits with no parseable location correctly counted as unknown (got {cov_unknown['unknown_location_jobs']})")
    check(cov_unknown["covered_cities"] == [], f"2: no city is guessed/invented for unparseable hits, got {cov_unknown['covered_cities']}")
    check(
        set(cov_unknown["still_uncovered"]) == {"Bengaluru", "Hyderabad", "Pune", "Chennai"},
        f"2: all 4 configured cities correctly remain 'not yet confirmed' (never asserted as proven-zero), got {cov_unknown['still_uncovered']}",
    )

    # ------------------------------------------------------------------
    # 3. Unknown metadata does not cause runaway query expansion
    #    (volume/new/completeness ALL sufficient, only location is
    #    unknown -- with the cap at 0, ZERO extra queries should fire)
    # ------------------------------------------------------------------
    check(
        res_unknown.adaptive_metrics["adaptive_queries"] == 0,
        f"3: max_additional_query_for_unknown_location=0 -- sufficient volume + all-unknown-location triggers ZERO extra queries (got {res_unknown.adaptive_metrics['adaptive_queries']})",
    )
    check(
        res_unknown.adaptive_metrics["queries_not_triggered_due_to_unknown_location"] >= 1,
        f"3: the suppressed expansion attempt is explicitly counted, not silently dropped (got {res_unknown.adaptive_metrics['queries_not_triggered_due_to_unknown_location']})",
    )

    # With the default cap (1), exactly one extra query is allowed --
    # never more, even though every location is still "unconfirmed".
    _, _, _, _, res_unknown_default_cap = _run_scenario("unknown_default_cap", fixture_unknown)
    check(
        res_unknown_default_cap.adaptive_metrics["adaptive_queries"] <= 1,
        f"3: default cap (1) allows AT MOST 1 extra query for a purely location-driven gap (got {res_unknown_default_cap.adaptive_metrics['adaptive_queries']})",
    )
    check(
        res_unknown_default_cap.adaptive_metrics["queries_triggered_by_location_gap"] <= 1,
        f"3: queries_triggered_by_location_gap correctly caps at 1 (got {res_unknown_default_cap.adaptive_metrics['queries_triggered_by_location_gap']})",
    )

    # ------------------------------------------------------------------
    # 4. Query budgets still work (reused from Phase 14.7's own check,
    #    re-verified under the new location-vs-volume split logic)
    # ------------------------------------------------------------------
    fixture_zero = {_query_text(ALL_LOCATIONS): []}
    _, _, cfg_budget, _, res_budget = _run_scenario("budget", fixture_zero, cfg_overrides={"max_search_provider_queries_per_run": 1})
    total_budget = res_budget.adaptive_metrics["consolidated_queries"] + res_budget.adaptive_metrics["adaptive_queries"]
    check(total_budget <= 1, f"4: global max_search_provider_queries_per_run=1 still enforced (got {total_budget})")

    # ------------------------------------------------------------------
    # 5. Cooldown still works
    # ------------------------------------------------------------------
    db_path_c, tmp_dir_c = _new_isolated_db()
    candidate_id_c = "cand_test_14_8_cooldown"
    _seed_confirmed_candidate(db_path_c, candidate_id_c)
    history_path_c = tmp_dir_c / "history.json"
    original_history_path = history_store.HISTORY_STORE_PATH
    history_store.HISTORY_STORE_PATH = history_path_c
    try:
        cfg_c = _cfg()
        conn = sqlite3.connect(db_path_c)
        conn.execute("PRAGMA foreign_keys = ON")
        plan_c1 = dsp.build_daily_plan(conn, candidate_id_c, target_roles_override=[ROLE], target_locations_override=ALL_LOCATIONS, config=cfg_c)
        conn.close()
        spa.set_search_provider(ReplayProvider(fixture_data=fixture_known))
        try:
            dsp.execute_daily_plan(db_path_c, candidate_id_c, plan_c1, target_roles_override=[ROLE], target_locations_override=ALL_LOCATIONS, config=cfg_c)
        finally:
            spa.clear_search_provider()

        conn = sqlite3.connect(db_path_c)
        conn.execute("PRAGMA foreign_keys = ON")
        plan_c2 = dsp.build_daily_plan(conn, candidate_id_c, target_roles_override=[ROLE], target_locations_override=ALL_LOCATIONS, config=cfg_c)
        conn.close()
    finally:
        history_store.HISTORY_STORE_PATH = original_history_path
    check(plan_c2.estimated_provider_calls == 0, f"5: cooldown still skips an immediate identical re-plan (got {plan_c2.estimated_provider_calls} estimated calls)")

    # ------------------------------------------------------------------
    # 6. Adaptive queries still target genuinely uncovered locations
    #    when evidence supports doing so (mix of known + unknown)
    # ------------------------------------------------------------------
    fixture_mixed = {
        _query_text(ALL_LOCATIONS): (
            [_hit_known(i, "Bangalore") for i in range(2)]
            + [_hit_unknown(50 + i) for i in range(2)]
        ),
        _query_text(["Chennai", "Hyderabad", "Pune"]): [_hit_known(100 + i, city) for i, city in enumerate(["Hyderabad", "Pune", "Chennai"])],
    }
    _, _, _, _, res_mixed = _run_scenario("mixed", fixture_mixed)
    cov_mixed = res_mixed.coverage["LINKEDIN"]
    check(
        set(cov_mixed["covered_cities"]) >= {"Bengaluru", "Hyderabad", "Pune", "Chennai"},
        f"6: a genuine coverage gap (3 real missing cities) still gets targeted and filled even though some hits were unknown, got {cov_mixed['covered_cities']}",
    )
    check(cov_mixed["unknown_location_jobs"] == 2, f"6: the 2 genuinely-unparseable hits are still counted as unknown, not silently dropped (got {cov_mixed['unknown_location_jobs']})")

except Exception as error:
    import traceback

    traceback.print_exc()
    failed += 1
    print(f"FAIL: unexpected exception: {error}")

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, "production DB untouched (SHA unchanged)")

print()
print(f"TOTAL: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
