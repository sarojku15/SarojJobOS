#!/usr/bin/env python3

"""
Tests for scripts/search_profile.py, scripts/search_submission.py,
scripts/query_planner.py's build_queries_from_search_profile(), and
scripts/submit_search.py.

Every test that touches SQLite uses its own fresh, isolated temporary
database (schema built via init_tracker.main() +
migrate_v2_schema._create_new_tables()/_ensure_job_columns() --
deliberately NOT migrate_v2_schema.migrate(), which seeds from the real
config/profile.json and would pull in Saroj-specific data). None of
these tests ever open data/applications/jobos.db.
"""

import inspect
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import init_tracker
import migrate_v2_schema
import query_planner
import search_profile as search_profile_module
import search_submission as search_submission_module
from candidate_profile import (
    normalize_candidate_profile,
    promote_to_confirmed,
    serialize_candidate_profile,
)
from query_planner import build_queries_from_search_profile
from search_profile import SearchProfile, SearchProfileError, build_search_profile
from search_submission import (
    SearchSubmissionError,
    build_search_plan,
    submit_search,
)


CLI_PATH = ROOT / "scripts" / "submit_search.py"


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


# ----------------------------------------------------------------------
# Isolated DB fixture helpers
# ----------------------------------------------------------------------

def _new_isolated_db():
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_search_submission_"))
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


def _seed_candidate(
    db_path,
    candidate_id,
    name="Test Candidate",
    status="ACTIVE",
    profile_status="CONFIRMED",
    target_roles=None,
    target_locations=None,
    experience_years=6,
):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    now = "2026-09-20T00:00:00+00:00"

    raw = {
        "identity": {"candidate_id": candidate_id, "name": name},
        "professional_summary": {"total_experience_years": experience_years},
        "job_preferences": {
            "target_roles": target_roles if target_roles is not None else ["Role A"],
            "target_locations": target_locations if target_locations is not None else ["Pune"],
        },
    }

    profile = normalize_candidate_profile(raw)
    confirmed_by_user = 0

    if profile_status == "CONFIRMED":
        profile = promote_to_confirmed(profile)
        confirmed_by_user = 1

    conn.execute(
        "INSERT INTO candidates (candidate_id, name, email, phone, created_at, updated_at, status) "
        "VALUES (?, ?, NULL, NULL, ?, ?, ?)",
        (candidate_id, name, now, now, status),
    )
    conn.execute(
        "INSERT INTO candidate_search_profile "
        "(candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) "
        "VALUES (?, 1, 'PROFILE', ?, ?, 1, ?, ?)",
        (candidate_id, json.dumps(serialize_candidate_profile(profile)), confirmed_by_user, now, now),
    )
    conn.commit()
    conn.close()


def _run_cli(args):
    return subprocess.run(
        [sys.executable, str(CLI_PATH)] + args,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


# ----------------------------------------------------------------------
# 1-4: candidate/profile gating
# ----------------------------------------------------------------------

def test_1_confirmed_candidate_can_generate_search_plan():
    failures = []
    db = _new_isolated_db()
    _seed_candidate(db, "cand-1", target_roles=["SRE"], target_locations=["Bengaluru"])

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        sp, plan = build_search_plan(conn, "cand-1")
        if not plan:
            _fail(failures, "test 1: expected a non-empty query plan for a valid CONFIRMED candidate")
        else:
            print(f"PASS: test 1 -> confirmed candidate generates a search plan ({len(plan)} quer{'y' if len(plan)==1 else 'ies'})")
    finally:
        conn.close()

    return failures


def test_2_draft_candidate_cannot_submit():
    failures = []
    db = _new_isolated_db()
    _seed_candidate(db, "cand-2", profile_status="DRAFT")

    try:
        submit_search(str(db), "cand-2", dry_run=True)
        _fail(failures, "test 2: expected SearchSubmissionError for a DRAFT profile")
    except SearchSubmissionError as error:
        if "DRAFT" not in str(error):
            _fail(failures, f"test 2: expected the error to mention DRAFT, got: {error}")
        else:
            print("PASS: test 2 -> a DRAFT candidate profile cannot submit a search")

    return failures


def test_3_unknown_candidate_fails_cleanly():
    failures = []
    db = _new_isolated_db()

    try:
        submit_search(str(db), "cand-does-not-exist", dry_run=True)
        _fail(failures, "test 3: expected SearchSubmissionError for an unknown candidate")
    except SearchSubmissionError as error:
        if "Unknown candidate" not in str(error):
            _fail(failures, f"test 3: expected an 'Unknown candidate' message, got: {error}")
        else:
            print("PASS: test 3 -> an unknown candidate fails cleanly with a clear error")

    return failures


def test_4_inactive_candidate_fails_cleanly():
    failures = []
    db = _new_isolated_db()
    _seed_candidate(db, "cand-4", status="INACTIVE")

    try:
        submit_search(str(db), "cand-4", dry_run=True)
        _fail(failures, "test 4: expected SearchSubmissionError for an inactive candidate")
    except SearchSubmissionError as error:
        if "not ACTIVE" not in str(error):
            _fail(failures, f"test 4: expected a 'not ACTIVE' message, got: {error}")
        else:
            print("PASS: test 4 -> an inactive candidate fails cleanly with a clear error")

    return failures


# ----------------------------------------------------------------------
# 5: search-profile validation
# ----------------------------------------------------------------------

def test_5_search_profile_validation():
    failures = []
    db = _new_isolated_db()
    _seed_candidate(db, "cand-5", target_roles=[], target_locations=["Pune"])

    try:
        submit_search(str(db), "cand-5", dry_run=True)
        _fail(failures, "test 5: expected SearchSubmissionError for a profile with no target_roles")
    except SearchSubmissionError as error:
        if "target_roles" not in str(error):
            _fail(failures, f"test 5: expected a target_roles-related error, got: {error}")
        else:
            print("PASS: test 5 -> a search profile with no target_roles is rejected with a clear message")

    return failures


# ----------------------------------------------------------------------
# 6-13: query generation
# ----------------------------------------------------------------------

def _plan_for(roles, locations, sources=("NAUKRI",)):
    sp = SearchProfile(
        candidate_id="cand-plan",
        target_roles=list(roles),
        excluded_roles=[],
        target_locations=list(locations),
        work_models=[],
        minimum_experience_years=None,
        maximum_experience_years=None,
        sources=list(sources),
        minimum_match_score=70,
        maximum_results=None,
        active=True,
        profile_version=1,
        confirmed_by_user=True,
    )
    return build_queries_from_search_profile(sp)


def test_6_multiple_roles_generate_deterministic_queries():
    failures = []
    plan_a = _plan_for(["SRE", "DevOps Engineer"], ["Bengaluru"])
    plan_b = _plan_for(["SRE", "DevOps Engineer"], ["Bengaluru"])

    if plan_a != plan_b:
        _fail(failures, "test 6: expected identical, deterministic output for identical input")
    elif len(plan_a) != 2:
        _fail(failures, f"test 6: expected 2 queries (2 roles x 1 location), got {len(plan_a)}")
    else:
        print("PASS: test 6 -> multiple roles generate deterministic queries")

    return failures


def test_7_multiple_locations_generate_deterministic_queries():
    failures = []
    plan_a = _plan_for(["SRE"], ["Bengaluru", "Hyderabad", "Pune"])
    plan_b = _plan_for(["SRE"], ["Bengaluru", "Hyderabad", "Pune"])

    if plan_a != plan_b:
        _fail(failures, "test 7: expected identical, deterministic output for identical input")
    elif len(plan_a) != 3:
        _fail(failures, f"test 7: expected 3 queries (1 role x 3 locations), got {len(plan_a)}")
    else:
        print("PASS: test 7 -> multiple locations generate deterministic queries")

    return failures


def test_8_query_count_is_deterministic():
    failures = []
    plan = _plan_for(["A", "B", "C"], ["X", "Y"])

    if len(plan) != 6:
        _fail(failures, f"test 8: expected 3 roles x 2 locations = 6 queries, got {len(plan)}")
    else:
        print(f"PASS: test 8 -> query count is exactly roles x locations = {len(plan)}")

    return failures


def test_9_existing_role_normalization_is_reused():
    """
    Confirms query generation does not re-implement its own role
    normalization -- roles pass through exactly as the candidate's
    confirmed profile states them, with only trivial
    dedup/sort applied.
    """
    failures = []
    plan = _plan_for(["Senior SRE", "senior sre"], ["Pune"])
    # These are NOT deduplicated by this module (no role-name
    # normalization is invented here) -- both distinct strings appear.
    roles_seen = {q["role"] for q in plan}

    if roles_seen != {"Senior SRE", "senior sre"}:
        _fail(failures, f"test 9: expected both distinct role strings preserved verbatim (no invented normalization), got {roles_seen}")
    else:
        print("PASS: test 9 -> role strings pass through verbatim -- no second role-normalization layer invented here")

    return failures


def test_10_existing_location_taxonomy_is_reused():
    failures = []
    source = inspect.getsource(search_profile_module)

    if "from location_taxonomy import" not in source:
        _fail(failures, "test 10: search_profile.py does not import from location_taxonomy.py")
    if "normalize_location_text(" not in source:
        _fail(failures, "test 10: search_profile.py does not call location_taxonomy.normalize_location_text()")

    # And no independent location-parsing regex/dict of its own.
    if "_CITY_ALIASES" in source or "_COMPOUND_PHRASES" in source:
        _fail(failures, "test 10: search_profile.py appears to define its own location alias table")

    if not failures:
        print("PASS: test 10 -> search_profile.py reuses location_taxonomy.py; no second location taxonomy is defined")

    return failures


def test_11_no_duplicate_queries_generated():
    failures = []
    # Deliberately repeat a role and a location.
    plan = _plan_for(["SRE", "SRE", "DevOps"], ["Pune", "Pune"])
    keys = [(q["role"], q["location"]) for q in plan]

    if len(keys) != len(set(keys)):
        _fail(failures, f"test 11: expected no duplicate (role, location) pairs, got {keys}")
    elif len(plan) != 2:
        _fail(failures, f"test 11: expected exactly 2 unique combinations (SRE/Pune, DevOps/Pune), got {len(plan)}")
    else:
        print("PASS: test 11 -> repeated roles/locations do not produce duplicate queries")

    return failures


def test_12_search_source_list_is_preserved():
    failures = []
    db = _new_isolated_db()
    _seed_candidate(db, "cand-12", target_roles=["SRE"], target_locations=["Pune"])

    result = submit_search(str(db), "cand-12", sources=["NAUKRI"], dry_run=True)

    if result.sources != ["NAUKRI"]:
        _fail(failures, f"test 12: expected sources=['NAUKRI'], got {result.sources}")
    else:
        print("PASS: test 12 -> explicitly requested search sources are preserved in the result")

    return failures


def test_13_minimum_score_is_preserved():
    failures = []
    db = _new_isolated_db()
    _seed_candidate(db, "cand-13", target_roles=["SRE"], target_locations=["Pune"])

    result = submit_search(str(db), "cand-13", minimum_match_score=85, dry_run=True)

    if result.minimum_match_score != 85:
        _fail(failures, f"test 13: expected minimum_match_score=85, got {result.minimum_match_score}")
    else:
        print("PASS: test 13 -> explicitly requested minimum match score is preserved")

    return failures


# ----------------------------------------------------------------------
# 14-17: DB writes
# ----------------------------------------------------------------------

def test_14_search_run_created_as_queued():
    failures = []
    db = _new_isolated_db()
    _seed_candidate(db, "cand-14", target_roles=["SRE"], target_locations=["Pune"])

    submit_search(str(db), "cand-14", dry_run=False)

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status FROM search_runs WHERE candidate_id = ?", ("cand-14",)).fetchone()
    conn.close()

    if row is None or row[0] != "QUEUED":
        _fail(failures, f"test 14: expected a search_runs row with status=QUEUED, got {row}")
    else:
        print("PASS: test 14 -> search_run is created with status=QUEUED")

    return failures


def test_15_queue_entry_created_as_queued():
    failures = []
    db = _new_isolated_db()
    _seed_candidate(db, "cand-15", target_roles=["SRE"], target_locations=["Pune"])

    submit_search(str(db), "cand-15", dry_run=False)

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status FROM search_queue WHERE candidate_id = ?", ("cand-15",)).fetchone()
    conn.close()

    if row is None or row[0] != "QUEUED":
        _fail(failures, f"test 15: expected a search_queue row with status=QUEUED, got {row}")
    else:
        print("PASS: test 15 -> queue entry is created with status=QUEUED")

    return failures


def test_16_queue_references_correct_search_run_id():
    failures = []
    db = _new_isolated_db()
    _seed_candidate(db, "cand-16", target_roles=["SRE"], target_locations=["Pune"])

    result = submit_search(str(db), "cand-16", dry_run=False)

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT search_run_id FROM search_queue WHERE candidate_id = ?", ("cand-16",)).fetchone()
    conn.close()

    if row is None or row[0] != result.search_run_id:
        _fail(failures, f"test 16: expected queue entry to reference search_run_id={result.search_run_id!r}, got {row}")
    else:
        print("PASS: test 16 -> queue entry correctly references the created search_run_id")

    return failures


def test_17_queue_preserves_source_and_query_information():
    """
    The queue entry's linked search_run row must carry the full,
    reproducible query plan and source list -- checked via the
    search_runs.query JSON snapshot the queue entry's search_run_id
    points to (search_queue itself has no source/query columns in the
    existing schema; that information lives one hop away on its
    parent search_run, which is exactly what search_queue.search_run_id
    is for).
    """
    failures = []
    db = _new_isolated_db()
    _seed_candidate(db, "cand-17", target_roles=["SRE", "DevOps"], target_locations=["Pune", "Bengaluru"])

    # Explicit sources=["NAUKRI"] -- this test verifies snapshot/storage
    # correctness, not which sources are currently ENABLED by default
    # (that set legitimately grows over time, e.g. Phase 11 enabled
    # HIRIST too; pinning the source list here keeps this test's true
    # intent isolated from that unrelated registry state).
    result = submit_search(str(db), "cand-17", sources=["NAUKRI"], dry_run=False)

    conn = sqlite3.connect(db)
    run_row = conn.execute(
        "SELECT source, query FROM search_runs WHERE search_run_id = ?", (result.search_run_id,)
    ).fetchone()
    conn.close()

    if run_row is None:
        _fail(failures, "test 17: could not find the search_run row")
        return failures

    source, query_json = run_row
    snapshot = json.loads(query_json)

    if source != "NAUKRI":
        _fail(failures, f"test 17: expected source='NAUKRI', got {source!r}")
    if len(snapshot.get("queries", [])) != 4:
        _fail(failures, f"test 17: expected 4 queries in the snapshot (2 roles x 2 locations), got {len(snapshot.get('queries', []))}")

    if not failures:
        print("PASS: test 17 -> the queued search_run preserves the full source and query-plan information the queue entry references")

    return failures


# ----------------------------------------------------------------------
# 18: no confirm => no write
# ----------------------------------------------------------------------

def test_18_no_write_without_explicit_confirmation():
    failures = []
    db = _new_isolated_db()
    _seed_candidate(db, "cand-18", target_roles=["SRE"], target_locations=["Pune"])

    submit_search(str(db), "cand-18", dry_run=True)

    conn = sqlite3.connect(db)
    run_count = conn.execute("SELECT COUNT(*) FROM search_runs").fetchone()[0]
    queue_count = conn.execute("SELECT COUNT(*) FROM search_queue").fetchone()[0]
    conn.close()

    if run_count != 0 or queue_count != 0:
        _fail(failures, f"test 18: expected zero DB writes on a dry run, got search_runs={run_count} search_queue={queue_count}")
    else:
        print("PASS: test 18 -> a dry-run submission (no explicit confirmation) writes nothing to the database")

    # Also via the actual CLI, without --confirm.
    cli_result = _run_cli(["--db", str(db), "--candidate-id", "cand-18"])
    conn = sqlite3.connect(db)
    run_count_after_cli = conn.execute("SELECT COUNT(*) FROM search_runs").fetchone()[0]
    conn.close()

    if run_count_after_cli != 0:
        _fail(failures, "test 18: expected the CLI without --confirm to also make zero DB writes")
    elif "PREVIEW only" not in cli_result.stdout:
        _fail(failures, f"test 18: expected the CLI to say this was a PREVIEW only, got: {cli_result.stdout[-300:]}")
    else:
        print("PASS: test 18 -> the CLI without --confirm also performs zero database writes")

    return failures


# ----------------------------------------------------------------------
# 19: rollback
# ----------------------------------------------------------------------

def test_19_failed_queue_creation_rolls_back_search_run():
    """
    Forces the search_queue INSERT to fail (by dropping the
    search_queue table in this isolated temp DB right before
    submitting) and confirms no search_runs row is left behind for
    this candidate -- the whole transaction rolled back, not just the
    failing half.
    """
    failures = []
    db = _new_isolated_db()
    _seed_candidate(db, "cand-19", target_roles=["SRE"], target_locations=["Pune"])

    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE search_queue")
    conn.commit()
    conn.close()

    try:
        submit_search(str(db), "cand-19", dry_run=False)
        _fail(failures, "test 19: expected submit_search() to raise when search_queue is missing")
    except sqlite3.OperationalError:
        pass
    except Exception as error:
        _fail(failures, f"test 19: expected a sqlite3.OperationalError, got {type(error).__name__}: {error}")

    conn = sqlite3.connect(db)
    try:
        run_count = conn.execute(
            "SELECT COUNT(*) FROM search_runs WHERE candidate_id = ?", ("cand-19",)
        ).fetchone()[0]
    finally:
        conn.close()

    if run_count != 0:
        _fail(failures, f"test 19: expected the search_runs insert to be rolled back (0 rows), got {run_count}")
    elif not failures:
        print("PASS: test 19 -> a failure creating the queue entry rolls back the already-inserted search_run row (atomic transaction)")

    return failures


# ----------------------------------------------------------------------
# 20: duplicate-submission policy
# ----------------------------------------------------------------------

def test_20_repeated_identical_submission_is_deterministic():
    failures = []
    db = _new_isolated_db()
    _seed_candidate(db, "cand-20", target_roles=["SRE"], target_locations=["Pune"])

    first = submit_search(str(db), "cand-20", dry_run=False)
    second = submit_search(str(db), "cand-20", dry_run=False)
    third = submit_search(str(db), "cand-20", dry_run=False)

    conn = sqlite3.connect(db)
    run_count = conn.execute("SELECT COUNT(*) FROM search_runs WHERE candidate_id = ?", ("cand-20",)).fetchone()[0]
    conn.close()

    if run_count != 1:
        _fail(failures, f"test 20: expected exactly 1 search_run row after 3 identical submissions, got {run_count}")
    if not first.submitted:
        _fail(failures, "test 20: expected the FIRST submission to actually be submitted (not a duplicate)")
    if second.submitted or second.duplicate_of != first.search_run_id:
        _fail(failures, f"test 20: expected the second identical submission to be recognized as a duplicate of {first.search_run_id!r}, got submitted={second.submitted} duplicate_of={second.duplicate_of!r}")
    if third.duplicate_of != first.search_run_id:
        _fail(failures, "test 20: expected the third identical submission to also resolve to the same original search_run_id")

    if not failures:
        print(f"PASS: test 20 -> repeated identical submissions deterministically resolve to the same QUEUED search_run ({first.search_run_id}), no duplicate rows created")

    return failures


# ----------------------------------------------------------------------
# 21-22: snapshot immutability
# ----------------------------------------------------------------------

def test_21_snapshot_preserves_submitted_preferences():
    failures = []
    db = _new_isolated_db()
    _seed_candidate(db, "cand-21", target_roles=["SRE", "DevOps"], target_locations=["Pune", "Bengaluru"])

    # Explicit sources=["NAUKRI"] -- see test 17's comment on why.
    result = submit_search(str(db), "cand-21", sources=["NAUKRI"], minimum_match_score=90, dry_run=False)

    conn = sqlite3.connect(db)
    query_json = conn.execute(
        "SELECT query FROM search_runs WHERE search_run_id = ?", (result.search_run_id,)
    ).fetchone()[0]
    conn.close()

    snapshot = json.loads(query_json)

    if snapshot["minimum_match_score"] != 90:
        _fail(failures, f"test 21: expected snapshot minimum_match_score=90, got {snapshot['minimum_match_score']}")
    if len(snapshot["queries"]) != 4:
        _fail(failures, f"test 21: expected 4 queries in the snapshot, got {len(snapshot['queries'])}")

    if not failures:
        print("PASS: test 21 -> the search-run snapshot preserves exactly what was submitted (roles, locations, min score)")

    return failures


def test_22_later_profile_changes_do_not_mutate_queued_snapshot():
    failures = []
    db = _new_isolated_db()
    _seed_candidate(db, "cand-22", target_roles=["SRE"], target_locations=["Pune"])

    result = submit_search(str(db), "cand-22", dry_run=False)

    conn = sqlite3.connect(db)
    before_snapshot = conn.execute(
        "SELECT query FROM search_runs WHERE search_run_id = ?", (result.search_run_id,)
    ).fetchone()[0]

    # Simulate the candidate later changing their confirmed profile
    # (new target_roles/locations) by writing a NEW active profile
    # version -- the already-QUEUED search_run must not change.
    now = "2026-09-20T01:00:00+00:00"
    new_profile = promote_to_confirmed(
        normalize_candidate_profile(
            {
                "identity": {"candidate_id": "cand-22", "name": "Test Candidate"},
                "professional_summary": {"total_experience_years": 6},
                "job_preferences": {
                    "target_roles": ["Completely Different Role"],
                    "target_locations": ["Chennai"],
                },
            }
        )
    )
    conn.execute("UPDATE candidate_search_profile SET is_active = 0 WHERE candidate_id = ?", ("cand-22",))
    conn.execute(
        "INSERT INTO candidate_search_profile "
        "(candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) "
        "VALUES (?, 2, 'PROFILE', ?, 1, 1, ?, ?)",
        ("cand-22", json.dumps(serialize_candidate_profile(new_profile)), now, now),
    )
    conn.commit()

    after_snapshot = conn.execute(
        "SELECT query FROM search_runs WHERE search_run_id = ?", (result.search_run_id,)
    ).fetchone()[0]
    conn.close()

    if before_snapshot != after_snapshot:
        _fail(failures, "test 22: the already-queued search_run's snapshot changed after the candidate's profile was updated -- it must be immutable")
    else:
        print("PASS: test 22 -> a later candidate-profile edit does not mutate an already-queued search_run's snapshot")

    return failures


# ----------------------------------------------------------------------
# 23-25
# ----------------------------------------------------------------------

def test_23_candidate_id_explicit_everywhere():
    failures = []

    for fn in (build_search_profile,):
        signature = inspect.signature(fn)
        if "profile" not in signature.parameters:
            _fail(failures, f"test 23: {fn.__name__}() does not take an explicit profile argument")

    for fn_name in ("load_candidate_confirmed_profile", "build_search_plan", "submit_search"):
        fn = getattr(search_submission_module, fn_name)
        signature = inspect.signature(fn)
        if "candidate_id" not in signature.parameters:
            _fail(failures, f"test 23: {fn_name}() does not take an explicit candidate_id argument")

    if not failures:
        print("PASS: test 23 -> candidate_id (or the candidate's own profile) is an explicit argument everywhere in this layer -- no implicit/global candidate")

    return failures


def test_24_no_saroj_specific_hardcoding():
    failures = []

    for module in (search_profile_module, search_submission_module):
        source = inspect.getsource(module)
        name_tokens_found = [t for t in ("Saroj", "Nayak") if t in source]
        if name_tokens_found:
            _fail(failures, f"test 24: {module.__name__} contains a literal candidate name token: {name_tokens_found}")
        if "config/profile.json" in source or "config/searches.json" in source:
            _fail(failures, f"test 24: {module.__name__} references a Saroj-specific config file")

    cli_source = CLI_PATH.read_text(encoding="utf-8")
    if "Saroj" in cli_source or "Nayak" in cli_source:
        _fail(failures, "test 24: submit_search.py contains a literal candidate name token")

    if not failures:
        print("PASS: test 24 -> no Saroj-specific hardcoding in search_profile.py, search_submission.py, or submit_search.py")

    return failures


def test_25_no_network_or_browser_execution():
    """
    Checks for an actual CALL with a real argument (e.g. get_adapter(
    source) or discover_from_sources(queries)) -- not a bare mention
    like "get_adapter()" in a docstring's prose explaining that this
    module does NOT call it, which several docstrings in this layer
    legitimately contain and which a naive substring check would
    misflag.
    """
    failures = []

    real_call_pattern = re.compile(
        r"(get_adapter|discover_from_sources)\(\s*[^)\s]"
    )

    for module in (search_profile_module, search_submission_module):
        source = inspect.getsource(module)

        forbidden_imports = ["import requests", "import urllib", "import http.client", "playwright", "subprocess"]
        found = [f for f in forbidden_imports if f in source]
        if found:
            _fail(failures, f"test 25: {module.__name__} references network/browser-related symbols: {found}")

        if real_call_pattern.search(source):
            _fail(failures, f"test 25: {module.__name__} appears to call a source adapter directly (search execution), which this component must never do")

    if not failures:
        print("PASS: test 25 -> no network/browser imports and no direct source-adapter execution calls anywhere in this layer")

    return failures


def main():
    tests = [
        test_1_confirmed_candidate_can_generate_search_plan,
        test_2_draft_candidate_cannot_submit,
        test_3_unknown_candidate_fails_cleanly,
        test_4_inactive_candidate_fails_cleanly,
        test_5_search_profile_validation,
        test_6_multiple_roles_generate_deterministic_queries,
        test_7_multiple_locations_generate_deterministic_queries,
        test_8_query_count_is_deterministic,
        test_9_existing_role_normalization_is_reused,
        test_10_existing_location_taxonomy_is_reused,
        test_11_no_duplicate_queries_generated,
        test_12_search_source_list_is_preserved,
        test_13_minimum_score_is_preserved,
        test_14_search_run_created_as_queued,
        test_15_queue_entry_created_as_queued,
        test_16_queue_references_correct_search_run_id,
        test_17_queue_preserves_source_and_query_information,
        test_18_no_write_without_explicit_confirmation,
        test_19_failed_queue_creation_rolls_back_search_run,
        test_20_repeated_identical_submission_is_deterministic,
        test_21_snapshot_preserves_submitted_preferences,
        test_22_later_profile_changes_do_not_mutate_queued_snapshot,
        test_23_candidate_id_explicit_everywhere,
        test_24_no_saroj_specific_hardcoding,
        test_25_no_network_or_browser_execution,
    ]

    all_failures = []
    for test in tests:
        all_failures.extend(test())

    print()

    if all_failures:
        print(f"{len(all_failures)} failure(s):")
        for failure in all_failures:
            print(f"  - {failure}")
        sys.exit(1)

    print(f"All {len(tests)} search-submission tests passed.")
    print("Note: this test file never opened data/applications/jobos.db -- every test used its own isolated temporary SQLite database.")


if __name__ == "__main__":
    main()
