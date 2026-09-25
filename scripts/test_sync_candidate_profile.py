#!/usr/bin/env python3

"""
Tests for scripts/sync_candidate_profile.py -- the production
candidate_search_profile reconciliation tool.

Every test that touches SQLite uses its own fresh, isolated temporary
database (schema via init_tracker.main() +
migrate_v2_schema._create_new_tables()/_ensure_job_columns()). None of
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
import sync_candidate_profile as sync_module
from candidate_profile import (
    ProfileStatus,
    Provenance,
    normalize_candidate_profile,
    promote_to_confirmed,
    serialize_candidate_profile,
)
from candidate_profile_store import save_candidate_profile_draft
from search_submission import build_search_plan
from sync_candidate_profile import (
    SyncError,
    inspect_existing_row,
    is_legacy_profile_shape,
    load_profile_to_sync,
    sync_candidate_profile,
)


CLI_PATH = ROOT / "scripts" / "sync_candidate_profile.py"


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _new_isolated_db():
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_sync_profile_"))
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


_LEGACY_PROFILE = {
    "candidate": {"name": "Test Candidate", "experience_years": 6},
    "target_roles": ["Some Role"],
    "target_locations": ["Pune"],
    "cloud": ["AWS"],
}


def _seed_candidate_row(db_path, candidate_id="cand-1", status="ACTIVE", name="Test Candidate"):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    now = "2026-09-20T00:00:00+00:00"
    conn.execute(
        "INSERT INTO candidates (candidate_id, name, email, phone, created_at, updated_at, status) "
        "VALUES (?, ?, NULL, NULL, ?, ?, ?)",
        (candidate_id, name, now, now, status),
    )
    conn.commit()
    conn.close()


def _seed_legacy_search_profile_row(db_path, candidate_id="cand-1", version=1):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    now = "2026-09-20T00:00:00+00:00"
    conn.execute(
        "INSERT INTO candidate_search_profile "
        "(candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) "
        "VALUES (?, ?, 'PROFILE', ?, 1, 1, ?, ?)",
        (candidate_id, version, json.dumps(_LEGACY_PROFILE), now, now),
    )
    conn.commit()
    conn.close()


def _confirmed_profile_file(tmp_dir, candidate_id="cand-1", target_roles=None, target_locations=None):
    raw = {
        "identity": {"candidate_id": candidate_id, "name": "Test Candidate"},
        "professional_summary": {"total_experience_years": 6},
        "job_preferences": {
            "target_roles": target_roles if target_roles is not None else ["Role A"],
            "target_locations": target_locations if target_locations is not None else ["Pune"],
        },
    }
    profile = promote_to_confirmed(normalize_candidate_profile(raw))
    path = tmp_dir / "confirmed.json"
    save_candidate_profile_draft(profile, path)
    return path


def _run_cli(args):
    return subprocess.run(
        [sys.executable, str(CLI_PATH)] + args,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


# ----------------------------------------------------------------------
# 1-2: shape detection / serialization
# ----------------------------------------------------------------------

def test_1_canonical_confirmed_profile_serializes_correctly():
    failures = []
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_sync_serialize_"))
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-1")

    from candidate_profile_store import load_candidate_profile_draft
    profile = load_candidate_profile_draft(profile_path)
    serialized = serialize_candidate_profile(profile)

    if "identity" not in serialized or "metadata" not in serialized:
        _fail(failures, f"test 1: expected canonical shape with identity/metadata keys, got {list(serialized.keys())}")
    elif serialized["metadata"]["profile_status"] != "CONFIRMED":
        _fail(failures, f"test 1: expected profile_status=CONFIRMED in serialized output, got {serialized['metadata']['profile_status']}")
    else:
        print("PASS: test 1 -> a canonical CONFIRMED profile serializes correctly via serialize_candidate_profile()")

    return failures


def test_2_legacy_profile_shape_is_detected():
    failures = []

    if not is_legacy_profile_shape(_LEGACY_PROFILE):
        _fail(failures, "test 2: expected the legacy flat profile shape to be detected as legacy")

    canonical_shaped = {"identity": {"candidate_id": "x"}, "metadata": {}}
    if is_legacy_profile_shape(canonical_shaped):
        _fail(failures, "test 2: expected a canonical-shaped profile to NOT be detected as legacy")

    if not failures:
        print("PASS: test 2 -> is_legacy_profile_shape() correctly distinguishes legacy vs canonical shapes")

    return failures


# ----------------------------------------------------------------------
# 3-7: rejection paths
# ----------------------------------------------------------------------

def test_3_draft_profile_cannot_be_synchronized():
    failures = []
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_sync_draft_"))
    draft = normalize_candidate_profile(
        {"identity": {"candidate_id": "cand-1"}, "job_preferences": {"target_roles": ["A"], "target_locations": ["Pune"]}}
    )
    path = tmp_dir / "draft.json"
    save_candidate_profile_draft(draft, path)

    try:
        load_profile_to_sync(path, "cand-1")
        _fail(failures, "test 3: expected SyncError for a DRAFT profile")
    except SyncError as error:
        if "DRAFT" not in str(error) and "not CONFIRMED" not in str(error):
            _fail(failures, f"test 3: expected a DRAFT/not-CONFIRMED error, got: {error}")
        else:
            print("PASS: test 3 -> a DRAFT profile cannot be synchronized")

    return failures


def test_4_unconfirmed_profile_cannot_be_synchronized():
    """
    A profile whose profile_status literally says CONFIRMED but whose
    confirmed_by_user flag is False (bypassing promote_to_confirmed(),
    e.g. a hand-edited file) must still be refused.
    """
    failures = []
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_sync_unconfirmed_"))
    profile = normalize_candidate_profile(
        {
            "identity": {"candidate_id": "cand-1"},
            "job_preferences": {"target_roles": ["A"], "target_locations": ["Pune"]},
            "metadata": {"profile_status": "CONFIRMED", "confirmed_by_user": False},
        }
    )
    path = tmp_dir / "inconsistent.json"
    save_candidate_profile_draft(profile, path)

    try:
        load_profile_to_sync(path, "cand-1")
        _fail(failures, "test 4: expected SyncError for confirmed_by_user=False")
    except SyncError as error:
        if "confirmed_by_user" not in str(error):
            _fail(failures, f"test 4: expected a confirmed_by_user-related error, got: {error}")
        else:
            print("PASS: test 4 -> an internally-inconsistent (CONFIRMED but confirmed_by_user=False) profile is refused")

    return failures


def test_5_wrong_candidate_id_is_rejected():
    failures = []
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_sync_wrongid_"))
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-A")

    try:
        load_profile_to_sync(profile_path, "cand-B")
        _fail(failures, "test 5: expected SyncError when the profile's candidate_id does not match the requested one")
    except SyncError as error:
        if "candidate_id" not in str(error):
            _fail(failures, f"test 5: expected a candidate_id mismatch error, got: {error}")
        else:
            print("PASS: test 5 -> a profile for a different candidate_id is rejected, never silently retargeted")

    return failures


def test_6_missing_candidate_is_rejected():
    failures = []
    db, tmp_dir = _new_isolated_db()
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-ghost")

    try:
        sync_candidate_profile(str(db), "cand-ghost", str(profile_path), confirm_production=False)
        _fail(failures, "test 6: expected SyncError for a candidate with no row in the candidates table")
    except SyncError as error:
        if "Unknown candidate" not in str(error):
            _fail(failures, f"test 6: expected an 'Unknown candidate' error, got: {error}")
        else:
            print("PASS: test 6 -> a candidate_id with no candidates row is rejected")

    return failures


def test_7_missing_profile_row_is_handled_correctly():
    failures = []
    db, tmp_dir = _new_isolated_db()
    _seed_candidate_row(db, candidate_id="cand-7")
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-7")

    # No candidate_search_profile row exists yet for cand-7.
    result = sync_candidate_profile(str(db), "cand-7", str(profile_path), confirm_production=True)

    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT version FROM candidate_search_profile WHERE candidate_id = ?", ("cand-7",)).fetchall()
    conn.close()

    if not result["written"]:
        _fail(failures, "test 7: expected the write to succeed for a candidate with no prior profile row")
    elif len(rows) != 1 or rows[0][0] != 1:
        _fail(failures, f"test 7: expected exactly one new row at version=1, got {rows}")
    else:
        print("PASS: test 7 -> a candidate with no existing candidate_search_profile row gets one correctly INSERTED (version=1)")

    return failures


# ----------------------------------------------------------------------
# 8-11: update-in-place behavior
# ----------------------------------------------------------------------

def test_8_existing_row_updated_not_duplicated():
    failures = []
    db, tmp_dir = _new_isolated_db()
    _seed_candidate_row(db, candidate_id="cand-8")
    _seed_legacy_search_profile_row(db, candidate_id="cand-8", version=1)
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-8")

    sync_candidate_profile(str(db), "cand-8", str(profile_path), confirm_production=True)

    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT profile_id, version FROM candidate_search_profile WHERE candidate_id = ?", ("cand-8",)).fetchall()
    conn.close()

    if len(rows) != 1:
        _fail(failures, f"test 8: expected exactly ONE row for cand-8 after sync (updated in place), got {len(rows)}: {rows}")
    elif rows[0] != (1, 1):
        _fail(failures, f"test 8: expected the SAME profile_id=1, version=1 preserved, got {rows[0]}")
    else:
        print("PASS: test 8 -> the existing row is updated in place (same profile_id/version), never duplicated")

    return failures


def test_9_transaction_rollback_works():
    """
    Forces the UPDATE to fail (by dropping candidate_search_profile
    right before syncing) and confirms the candidates table (and
    anything else) is left completely unaffected -- there's nothing to
    partially commit here since this is a single-statement write, but
    this proves the whole operation raises cleanly rather than leaving
    the DB in an inconsistent state.
    """
    failures = []
    db, tmp_dir = _new_isolated_db()
    _seed_candidate_row(db, candidate_id="cand-9")
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-9")

    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE candidate_search_profile")
    conn.commit()
    conn.close()

    try:
        sync_candidate_profile(str(db), "cand-9", str(profile_path), confirm_production=True)
        _fail(failures, "test 9: expected an exception when candidate_search_profile is missing")
    except sqlite3.OperationalError:
        pass
    except Exception as error:
        _fail(failures, f"test 9: expected sqlite3.OperationalError, got {type(error).__name__}: {error}")

    conn = sqlite3.connect(db)
    candidate_row = conn.execute("SELECT status FROM candidates WHERE candidate_id = ?", ("cand-9",)).fetchone()
    conn.close()

    if candidate_row is None or candidate_row[0] != "ACTIVE":
        _fail(failures, "test 9: the candidates row was unexpectedly affected by the failed sync attempt")
    elif not failures:
        print("PASS: test 9 -> a failure during the write raises cleanly and leaves other tables/rows unaffected (rollback)")

    return failures


def test_10_only_candidate_search_profile_is_modified():
    failures = []
    db, tmp_dir = _new_isolated_db()
    _seed_candidate_row(db, candidate_id="cand-10")
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-10")

    conn = sqlite3.connect(db)
    before_candidate = conn.execute("SELECT * FROM candidates WHERE candidate_id = ?", ("cand-10",)).fetchone()
    before_counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("jobs", "candidate_job_matches", "search_runs", "search_queue")
    }
    conn.close()

    sync_candidate_profile(str(db), "cand-10", str(profile_path), confirm_production=True)

    conn = sqlite3.connect(db)
    after_candidate = conn.execute("SELECT * FROM candidates WHERE candidate_id = ?", ("cand-10",)).fetchone()
    after_counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("jobs", "candidate_job_matches", "search_runs", "search_queue")
    }
    conn.close()

    if before_candidate != after_candidate:
        _fail(failures, f"test 10: the candidates row changed: {before_candidate} -> {after_candidate}")
    if before_counts != after_counts:
        _fail(failures, f"test 10: unrelated table row counts changed: {before_counts} -> {after_counts}")

    if not failures:
        print("PASS: test 10 -> only candidate_search_profile is modified; candidates/jobs/candidate_job_matches/search_runs/search_queue are untouched")

    return failures


def test_11_canonical_json_written_via_serializer():
    failures = []
    db, tmp_dir = _new_isolated_db()
    _seed_candidate_row(db, candidate_id="cand-11")
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-11", target_roles=["SRE"], target_locations=["Bengaluru"])

    sync_candidate_profile(str(db), "cand-11", str(profile_path), confirm_production=True)

    conn = sqlite3.connect(db)
    stored_json = conn.execute("SELECT profile_json FROM candidate_search_profile WHERE candidate_id = ?", ("cand-11",)).fetchone()[0]
    conn.close()

    stored = json.loads(stored_json)

    from candidate_profile_store import load_candidate_profile_draft as _load
    original_profile = _load(profile_path)
    expected = serialize_candidate_profile(original_profile)

    if stored != expected:
        _fail(failures, "test 11: stored profile_json does not exactly match serialize_candidate_profile()'s output")
    else:
        print("PASS: test 11 -> the stored profile_json is written exactly through serialize_candidate_profile(), no manual JSON construction")

    return failures


# ----------------------------------------------------------------------
# 12-14: write safety
# ----------------------------------------------------------------------

def test_12_confirm_production_flag_required_for_write():
    failures = []
    db, tmp_dir = _new_isolated_db()
    _seed_candidate_row(db, candidate_id="cand-12")
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-12")

    result = sync_candidate_profile(str(db), "cand-12", str(profile_path), confirm_production=False)

    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM candidate_search_profile WHERE candidate_id = ?", ("cand-12",)).fetchone()[0]
    conn.close()

    if result["written"]:
        _fail(failures, "test 12: expected written=False when confirm_production=False")
    elif count != 0:
        _fail(failures, f"test 12: expected zero rows written without --confirm-production, got {count}")
    else:
        print("PASS: test 12 -> --confirm-production (confirm_production=True) is required for any production mutation")

    return failures


def test_13_preview_mode_performs_zero_writes():
    failures = []
    db, tmp_dir = _new_isolated_db()
    _seed_candidate_row(db, candidate_id="cand-13")
    _seed_legacy_search_profile_row(db, candidate_id="cand-13")
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-13")

    before_bytes = db.read_bytes()

    cli_result = _run_cli(["--db", str(db), "--candidate-id", "cand-13", "--profile", str(profile_path)])

    after_bytes = db.read_bytes()

    if before_bytes != after_bytes:
        _fail(failures, "test 13: the database file changed on disk after a preview-only CLI run")
    elif "PREVIEW only" not in cli_result.stdout:
        _fail(failures, f"test 13: expected a PREVIEW-only message, got: {cli_result.stdout[-300:]}")
    else:
        print("PASS: test 13 -> preview mode (no --confirm-production) performs zero writes, byte-identical DB file")

    return failures


def test_14_production_update_preserves_unrelated_fields():
    failures = []
    db, tmp_dir = _new_isolated_db()
    _seed_candidate_row(db, candidate_id="cand-14", name="Original Name")
    _seed_legacy_search_profile_row(db, candidate_id="cand-14", version=1)
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-14")

    conn = sqlite3.connect(db)
    before_created_at = conn.execute(
        "SELECT created_at FROM candidate_search_profile WHERE candidate_id = ?", ("cand-14",)
    ).fetchone()[0]
    conn.close()

    sync_candidate_profile(str(db), "cand-14", str(profile_path), confirm_production=True)

    conn = sqlite3.connect(db)
    candidate_name = conn.execute("SELECT name FROM candidates WHERE candidate_id = ?", ("cand-14",)).fetchone()[0]
    after_created_at, after_version = conn.execute(
        "SELECT created_at, version FROM candidate_search_profile WHERE candidate_id = ?", ("cand-14",)
    ).fetchone()
    conn.close()

    if candidate_name != "Original Name":
        _fail(failures, f"test 14: expected candidates.name to remain 'Original Name', got {candidate_name!r}")
    if after_created_at != before_created_at:
        _fail(failures, f"test 14: expected created_at preserved ({before_created_at}), got {after_created_at}")
    if after_version != 1:
        _fail(failures, f"test 14: expected version to remain 1, got {after_version}")

    if not failures:
        print("PASS: test 14 -> production update preserves unrelated fields (candidates.name, created_at, version)")

    return failures


# ----------------------------------------------------------------------
# 15-19: post-sync effects
# ----------------------------------------------------------------------

def test_15_search_submission_validation_succeeds_after_sync():
    failures = []
    db, tmp_dir = _new_isolated_db()
    _seed_candidate_row(db, candidate_id="cand-15")
    _seed_legacy_search_profile_row(db, candidate_id="cand-15")
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-15", target_roles=["SRE"], target_locations=["Pune"])

    conn = sqlite3.connect(db)
    try:
        build_search_plan(conn, "cand-15")
        _fail(failures, "test 15: expected build_search_plan() to FAIL before synchronization (legacy profile shape)")
    except Exception:
        pass
    finally:
        conn.close()

    sync_candidate_profile(str(db), "cand-15", str(profile_path), confirm_production=True)

    conn = sqlite3.connect(db)
    try:
        search_profile, plan = build_search_plan(conn, "cand-15")
        if not plan:
            _fail(failures, "test 15: expected a non-empty query plan after synchronization")
        else:
            print("PASS: test 15 -> search_submission.build_search_plan() fails before sync (legacy shape) and succeeds after sync (canonical shape)")
    except Exception as error:
        _fail(failures, f"test 15: expected build_search_plan() to succeed after synchronization, got: {error}")
    finally:
        conn.close()

    return failures


def test_16_no_search_runs_created():
    failures = []
    db, tmp_dir = _new_isolated_db()
    _seed_candidate_row(db, candidate_id="cand-16")
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-16")

    sync_candidate_profile(str(db), "cand-16", str(profile_path), confirm_production=True)

    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM search_runs").fetchone()[0]
    conn.close()

    if count != 0:
        _fail(failures, f"test 16: expected zero search_runs rows, got {count}")
    else:
        print("PASS: test 16 -> no search_runs rows are created by synchronization")

    return failures


def test_17_no_search_queue_created():
    failures = []
    db, tmp_dir = _new_isolated_db()
    _seed_candidate_row(db, candidate_id="cand-17")
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-17")

    sync_candidate_profile(str(db), "cand-17", str(profile_path), confirm_production=True)

    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM search_queue").fetchone()[0]
    conn.close()

    if count != 0:
        _fail(failures, f"test 17: expected zero search_queue rows, got {count}")
    else:
        print("PASS: test 17 -> no search_queue rows are created by synchronization")

    return failures


def test_18_no_jobs_modified():
    failures = []
    db, tmp_dir = _new_isolated_db()
    _seed_candidate_row(db, candidate_id="cand-18")
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-18")

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO jobs (job_id, source, company, title) VALUES ('J1', 'TEST', 'Co', 'Title')"
    )
    conn.commit()
    before = conn.execute("SELECT * FROM jobs").fetchall()
    conn.close()

    sync_candidate_profile(str(db), "cand-18", str(profile_path), confirm_production=True)

    conn = sqlite3.connect(db)
    after = conn.execute("SELECT * FROM jobs").fetchall()
    conn.close()

    if before != after:
        _fail(failures, "test 18: the jobs table changed after synchronization")
    else:
        print("PASS: test 18 -> jobs table is completely unaffected by synchronization")

    return failures


def test_19_no_candidate_job_matches_modified():
    failures = []
    db, tmp_dir = _new_isolated_db()
    _seed_candidate_row(db, candidate_id="cand-19")
    profile_path = _confirmed_profile_file(tmp_dir, candidate_id="cand-19")

    conn = sqlite3.connect(db)
    now = "2026-09-20T00:00:00+00:00"
    conn.execute(
        "INSERT INTO jobs (job_id, source, company, title) VALUES ('J2', 'TEST', 'Co', 'Title')"
    )
    conn.execute(
        "INSERT INTO candidate_job_matches (candidate_id, job_id, created_at, updated_at) VALUES (?, 'J2', ?, ?)",
        ("cand-19", now, now),
    )
    conn.commit()
    before = conn.execute("SELECT * FROM candidate_job_matches").fetchall()
    conn.close()

    sync_candidate_profile(str(db), "cand-19", str(profile_path), confirm_production=True)

    conn = sqlite3.connect(db)
    after = conn.execute("SELECT * FROM candidate_job_matches").fetchall()
    conn.close()

    if before != after:
        _fail(failures, "test 19: candidate_job_matches changed after synchronization")
    else:
        print("PASS: test 19 -> candidate_job_matches is completely unaffected by synchronization")

    return failures


def test_20_no_network_or_browser_execution():
    failures = []
    source = inspect.getsource(sync_module)

    forbidden_imports = ["import requests", "import urllib", "import http.client", "playwright"]
    found = [f for f in forbidden_imports if f in source]
    if found:
        _fail(failures, f"test 20: sync_candidate_profile.py references network/browser-related symbols: {found}")

    real_call_pattern = re.compile(r"(get_adapter|discover_from_sources|submit_search)\(\s*[^)\s]")
    if real_call_pattern.search(source):
        _fail(failures, "test 20: sync_candidate_profile.py appears to call a source adapter or submit_search() -- it must only verify build_search_plan()")

    if not failures:
        print("PASS: test 20 -> no network/browser imports and no adapter/search-submission execution calls in sync_candidate_profile.py")

    return failures


def main():
    tests = [
        test_1_canonical_confirmed_profile_serializes_correctly,
        test_2_legacy_profile_shape_is_detected,
        test_3_draft_profile_cannot_be_synchronized,
        test_4_unconfirmed_profile_cannot_be_synchronized,
        test_5_wrong_candidate_id_is_rejected,
        test_6_missing_candidate_is_rejected,
        test_7_missing_profile_row_is_handled_correctly,
        test_8_existing_row_updated_not_duplicated,
        test_9_transaction_rollback_works,
        test_10_only_candidate_search_profile_is_modified,
        test_11_canonical_json_written_via_serializer,
        test_12_confirm_production_flag_required_for_write,
        test_13_preview_mode_performs_zero_writes,
        test_14_production_update_preserves_unrelated_fields,
        test_15_search_submission_validation_succeeds_after_sync,
        test_16_no_search_runs_created,
        test_17_no_search_queue_created,
        test_18_no_jobs_modified,
        test_19_no_candidate_job_matches_modified,
        test_20_no_network_or_browser_execution,
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

    print(f"All {len(tests)} sync-candidate-profile tests passed.")
    print("Note: this test file never opened data/applications/jobos.db -- every test used its own isolated temporary SQLite database.")


if __name__ == "__main__":
    main()
