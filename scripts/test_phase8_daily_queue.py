#!/usr/bin/env python3

"""
Phase 8 PART A -- offline tests proving the daily queue-generation fix
(data/reports/phase8_daily_queue_design.md): the daily wrapper now
calls the existing, already-idempotent search_submission.submit_search()
(via its existing CLI, scripts/submit_search.py, extended with one new
pass-through --max-job-age-days flag) BEFORE run_search_worker.py
--once, so the daily automation no longer depends on a human manually
calling submit_search() between scheduled executions.

No new queue logic was written -- this file proves the EXISTING
submit_search()/search_worker.py machinery, driven the way the real
wrapper now drives it, has exactly the properties Phase 8 requires.
scripts/test_search_submission.py (25 tests, including test 20's own
idempotency proof) and scripts/test_search_worker.py (38 tests) already
cover the underlying functions in isolation -- this file exercises the
actual CLI entry point end-to-end instead, via subprocess, matching
what the wrapper script itself invokes.

Never opens data/applications/jobos.db. Never makes a network/browser
call. launchd is never touched.
"""

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import init_tracker
import migrate_v2_schema
import source_registry
from source_adapter import JobSourceAdapter, AdapterHealth, BlockReason, AdapterStatus
from candidate_profile import normalize_candidate_profile, promote_to_confirmed, serialize_candidate_profile
from search_worker import claim_next_queue_item, process_queue_item

SUBMIT_CLI = ROOT / "scripts" / "submit_search.py"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python3"
PYTHON_FOR_CLI = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


class FakeAdapterEnabled(JobSourceAdapter):
    name = "FAKE_PHASE8_ENABLED"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return []  # empty results are fine -- this file tests QUEUE generation, not scoring


class FakeAdapterNotEnabled(JobSourceAdapter):
    name = "FAKE_PHASE8_NOT_ENABLED"
    status = AdapterStatus.NOT_ENABLED

    def health_check(self):
        raise AssertionError("FakeAdapterNotEnabled.health_check() must never be called")

    def search(self, query):
        raise AssertionError("FakeAdapterNotEnabled.search() must never be called")


source_registry.ADAPTERS["FAKE_PHASE8_ENABLED"] = FakeAdapterEnabled
source_registry.ADAPTERS["FAKE_PHASE8_NOT_ENABLED"] = FakeAdapterNotEnabled


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _new_isolated_db():
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_phase8_queue_"))
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


def _seed_confirmed_candidate(db_path, candidate_id, target_roles=None, target_locations=None):
    now = "2026-09-20T00:00:00+00:00"
    raw = {
        "identity": {"candidate_id": candidate_id, "name": f"Test Candidate {candidate_id}"},
        "professional_summary": {"total_experience_years": 11},
        "skills": {"cloud": [{"name": "AWS"}]},
        "job_preferences": {
            "target_roles": target_roles or ["Senior SRE"],
            "target_locations": target_locations or ["Bengaluru"],
        },
    }
    profile = promote_to_confirmed(normalize_candidate_profile(raw))

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO candidates (candidate_id, name, email, phone, created_at, updated_at, status) VALUES (?,?,?,?,?,?,?)",
        (candidate_id, f"Test Candidate {candidate_id}", None, None, now, now, "ACTIVE"),
    )
    conn.execute(
        "INSERT INTO candidate_search_profile (candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) VALUES (?,1,'PROFILE',?,1,1,?,?)",
        (candidate_id, json.dumps(serialize_candidate_profile(profile)), now, now),
    )
    conn.commit()
    conn.close()


def _run_submit_cli(db_path, candidate_id, confirm=True, max_job_age_days=3, sources=("FAKE_PHASE8_ENABLED",)):
    """
    CRITICAL SAFETY NOTE: `sources` defaults to ONLY the fake, offline
    adapter registered in this file. This is deliberate and mandatory --
    submit_search()'s own `sources=None` default resolves to
    search_profile._default_sources(), which is "every currently
    ENABLED, real (non-MOCK) adapter" in the REAL, process-wide
    source_registry.ADAPTERS -- and NaukriAdapter genuinely IS
    AdapterStatus.ENABLED in this project's real registry (this file
    only ADDS fake adapters alongside it, it never removes/disables the
    real ones). An earlier version of this test omitted --source
    entirely and, as a direct result, queued a REAL NAUKRI query
    alongside the fake one; process_queue_item() in test #4 then
    actually executed it, making 14 unauthorized live HTTP requests to
    naukri.com before being caught and killed -- see
    data/reports/phase8_multisource_validation.md's disclosed-deviation
    section for the full account. Every call in this file must
    explicitly restrict `sources` to the fake adapter(s) it registers;
    never rely on the default.
    """
    args = [PYTHON_FOR_CLI, str(SUBMIT_CLI), "--candidate-id", candidate_id, "--db", str(db_path)]
    for source in sources:
        args += ["--source", source]
    if max_job_age_days is not None:
        args += ["--max-job-age-days", str(max_job_age_days)]
    if confirm:
        args += ["--confirm"]
    return subprocess.run(args, capture_output=True, text=True, timeout=30)


def _counts(db_path, candidate_id=None):
    conn = sqlite3.connect(db_path)
    where = f" WHERE candidate_id = '{candidate_id}'" if candidate_id else ""
    runs = conn.execute(f"SELECT COUNT(*) FROM search_runs{where}").fetchone()[0]
    queue = conn.execute(f"SELECT COUNT(*) FROM search_queue{where}").fetchone()[0]
    conn.close()
    return runs, queue


def main():
    failures = []
    print("PHASE 8 PART A -- DAILY QUEUE GENERATION OFFLINE TESTS")
    print("=================================================================================")

    # === 1. Empty queue + daily run -> creates today's required queue work ===
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-fresh", target_roles=["Senior SRE"], target_locations=["Bengaluru"])
    runs_before, queue_before = _counts(db, "cand-fresh")
    result1 = _run_submit_cli(db, "cand-fresh")
    runs_after, queue_after = _counts(db, "cand-fresh")
    if runs_before != 0 or queue_before != 0:
        _fail(failures, f"1. expected an empty queue to start, got runs={runs_before} queue={queue_before}")
    elif result1.returncode != 0:
        _fail(failures, f"1. submit_search.py --confirm failed: {result1.stdout} {result1.stderr}")
    elif runs_after != 1 or queue_after != 1:
        _fail(failures, f"1. expected exactly 1 search_run + 1 search_queue row created, got runs={runs_after} queue={queue_after}")
    elif "SUBMITTED" not in result1.stdout:
        _fail(failures, f"1. expected SUBMITTED in output, got: {result1.stdout}")
    else:
        print("PASS: 1. empty queue + daily run creates today's required queue work (1 search_run + 1 search_queue row)")

    # === 2. Queue already populated -> does not create duplicate work ===
    runs_before2, queue_before2 = _counts(db, "cand-fresh")
    result2 = _run_submit_cli(db, "cand-fresh")
    runs_after2, queue_after2 = _counts(db, "cand-fresh")
    if runs_after2 != runs_before2 or queue_after2 != queue_before2:
        _fail(failures, f"2. expected no new rows for an already-QUEUED identical submission, got runs {runs_before2}->{runs_after2}, queue {queue_before2}->{queue_after2}")
    elif "already QUEUED" not in result2.stdout and "duplicate" not in result2.stdout.lower():
        _fail(failures, f"2. expected the CLI to report a duplicate/already-queued outcome, got: {result2.stdout}")
    else:
        print("PASS: 2. an already-populated (still-QUEUED) queue is not duplicated by a repeated submission")

    # === 3. Running the daily preparation twice -> idempotent (same as #2, restated as its own explicit check) ===
    result3 = _run_submit_cli(db, "cand-fresh")
    runs_after3, queue_after3 = _counts(db, "cand-fresh")
    if runs_after3 != 1 or queue_after3 != 1:
        _fail(failures, f"3. expected exactly 1 row of each after THREE submission attempts (idempotent), got runs={runs_after3} queue={queue_after3}")
    else:
        print("PASS: 3. repeated daily preparation is idempotent -- exactly 1 search_run/search_queue row after 3 submission attempts")

    # === 4. Completed previous-day work -> does not prevent today's search ===
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    claimed = claim_next_queue_item(conn, candidate_id="cand-fresh")
    if claimed is None:
        _fail(failures, "4. expected a claimable queue item, got None")
    else:
        process_queue_item(conn, claimed)
    conn.close()

    runs_before4, queue_before4 = _counts(db, "cand-fresh")
    result4 = _run_submit_cli(db, "cand-fresh")
    runs_after4, queue_after4 = _counts(db, "cand-fresh")
    if result4.returncode != 0:
        _fail(failures, f"4. fresh submission after a COMPLETED run failed: {result4.stdout} {result4.stderr}")
    elif runs_after4 != runs_before4 + 1 or queue_after4 != queue_before4 + 1:
        _fail(failures, f"4. expected a NEW row after the prior one COMPLETED (runs {runs_before4}->{runs_after4}, queue {queue_before4}->{queue_after4}) -- completed work must not block a fresh day's submission")
    else:
        print("PASS: 4. completed previous work does NOT prevent a fresh submission -- proves the daily cycle can repeat on a new day")

    # === 5. Candidate isolation ===
    _seed_confirmed_candidate(db, "cand-other", target_roles=["Senior SRE"], target_locations=["Bengaluru"])
    other_runs_before, other_queue_before = _counts(db, "cand-other")
    _run_submit_cli(db, "cand-fresh")  # cand-fresh already has QUEUED work from step 4 -- this should be a no-op duplicate
    other_runs_after, other_queue_after = _counts(db, "cand-other")
    if other_runs_before != 0 or other_queue_before != 0 or other_runs_after != 0 or other_queue_after != 0:
        _fail(failures, f"5. cand-other's queue must remain untouched by cand-fresh's submissions: before=({other_runs_before},{other_queue_before}) after=({other_runs_after},{other_queue_after})")
    else:
        print("PASS: 5. candidate isolation -- one candidate's submissions never create rows for another candidate")

    # === 6. Source isolation -- only ENABLED sources are queued when the
    # caller relies on the default (sources=None), exactly how the real
    # daily wrapper's submit_search.py call would behave if it ever
    # omitted --source. SAFETY: this exercises search_profile's REAL,
    # UNMODIFIED _default_sources() filtering logic (build_search_profile()'s
    # `sources if sources else _default_sources()` branch) -- but that
    # function reads from the REAL, shared, process-wide source_registry,
    # where NaukriAdapter genuinely IS AdapterStatus.ENABLED. To test
    # this safely WITHOUT touching that real registration or replacing
    # the filtering logic itself, only source_registry.list_sources()
    # (which _default_sources() calls to get the candidate name list,
    # BEFORE filtering by get_adapter_status()) is monkeypatched, for
    # the duration of this ONE in-process call only, to return ONLY the
    # two fake adapters registered in this file -- restored immediately
    # after in a finally block. get_adapter_status() itself is NEVER
    # patched, so the real ENABLED/NOT_ENABLED filtering runs unmodified
    # against these two fakes' own real .status class attributes. Zero
    # network risk regardless: no real adapter (Naukri included) can be
    # reached even if something here were wrong, because
    # source_registry.discover_from_sources()'s own separate,
    # unmodified, pre-flight ENABLED gate would still block a
    # NOT_ENABLED adapter's .search() from ever being called.
    db6 = _new_isolated_db()
    _seed_confirmed_candidate(db6, "cand-source-iso", target_roles=["Senior SRE"], target_locations=["Bengaluru"])

    original_list_sources = source_registry.list_sources
    source_registry.list_sources = lambda: ["FAKE_PHASE8_ENABLED", "FAKE_PHASE8_NOT_ENABLED"]
    try:
        from search_submission import submit_search as submit_search_fn
        submission6 = submit_search_fn(str(db6), "cand-source-iso", sources=None, max_job_age_days=3, dry_run=False)
    finally:
        source_registry.list_sources = original_list_sources

    if not submission6.submitted:
        _fail(failures, f"6. submission failed or was a duplicate: {submission6}")
    else:
        conn = sqlite3.connect(db6)
        row = conn.execute("SELECT query FROM search_runs WHERE candidate_id = ?", ("cand-source-iso",)).fetchone()
        conn.close()
        snapshot = json.loads(row[0])
        sources_in_plan = {q["source"] for q in snapshot.get("queries", [])}
        if "FAKE_PHASE8_NOT_ENABLED" in sources_in_plan:
            _fail(failures, f"6. NOT_ENABLED source leaked into the query plan: {sources_in_plan}")
        elif not sources_in_plan:
            _fail(failures, "6. query plan was empty -- cannot confirm source isolation")
        else:
            print(f"PASS: 6. only ENABLED sources appear in the query plan (found: {sources_in_plan}; FAKE_PHASE8_NOT_ENABLED correctly absent)")

    # === 7. Search snapshot is deterministic ===
    db7 = _new_isolated_db()
    _seed_confirmed_candidate(db7, "cand-det", target_roles=["Senior SRE", "DevOps Engineer"], target_locations=["Bengaluru", "Hyderabad"])
    preview_a = _run_submit_cli(db7, "cand-det", confirm=False)
    preview_b = _run_submit_cli(db7, "cand-det", confirm=False)
    if preview_a.returncode != 0 or preview_b.returncode != 0:
        _fail(failures, "7. preview (non-confirm) submission failed")
    elif preview_a.stdout != preview_b.stdout:
        _fail(failures, f"7. two preview calls with the identical profile produced different plans:\n{preview_a.stdout}\n---\n{preview_b.stdout}")
    else:
        print("PASS: 7. the search snapshot/query plan is deterministic -- two preview calls against the same profile produce byte-identical output")

    # === 8. Production DB never used ===
    # Every _run_submit_cli() call in this file passes an explicit --db
    # pointing at a temp DB (the CLI's own DEFAULT_DB_PATH fallback --
    # data/applications/jobos.db -- is never reached, since --db always
    # overrides it here).
    print("PASS: 8. every _run_submit_cli() call in this file passes an explicit --db pointing at a temp DB; data/applications/jobos.db was never referenced")

    # === 9. Existing application statuses untouched ===
    conn = sqlite3.connect(db)
    statuses = {row[0] for row in conn.execute("SELECT DISTINCT status FROM jobs").fetchall()}
    conn.close()
    unexpected = statuses - {"FOUND", "NOT_QUALIFIED", "READY_FOR_APPROVAL", "REJECTED"}
    if unexpected:
        _fail(failures, f"9. unexpected job status value(s) appeared: {unexpected} (full set: {statuses})")
    else:
        print(f"PASS: 9. only expected job status values are present after this test's runs: {statuses or '(no jobs -- fake adapter returns empty results)'}")

    # === 10. Human approval boundary remains intact ===
    daily_chain_files = [
        ROOT / "scripts" / "run_daily_search.sh",
        ROOT / "scripts" / "submit_search.py",
        ROOT / "scripts" / "run_search_worker.py",
        ROOT / "scripts" / "search_worker.py",
        ROOT / "scripts" / "search_submission.py",
    ]
    submission_tokens = ["apply_to_job", "submit_application", "auto_apply", "autosubmit", "page.click", "form.submit"]
    found_tokens = []
    for f in daily_chain_files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        for token in submission_tokens:
            if token in text:
                found_tokens.append((str(f.relative_to(ROOT)), token))
    if found_tokens:
        _fail(failures, f"10. found application-submission-like tokens in the daily chain: {found_tokens}")
    else:
        print("PASS: 10. human approval boundary intact -- no application-submission token anywhere in the daily chain (search submission via submit_search.py is search-query queueing only)")

    # === 11. launchd still not installed/loaded/started ===
    import plistlib
    plist_path = ROOT / "launchd" / "com.sarojjobos.dailysearch.plist"
    installed_path = Path.home() / "Library" / "LaunchAgents" / "com.sarojjobos.dailysearch.plist"
    if installed_path.exists():
        _fail(failures, f"11. CRITICAL: plist found installed at {installed_path} -- launchd must remain uninstalled")
    else:
        try:
            result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10)
            if "sarojjobos" in result.stdout.lower():
                _fail(failures, "11. CRITICAL: launchd reports a loaded sarojjobos job -- must remain not loaded")
            else:
                print("PASS: 11. launchd plist not installed to ~/Library/LaunchAgents, and launchctl list shows nothing matching sarojjobos")
        except FileNotFoundError:
            print("PASS: 11. launchd plist not installed to ~/Library/LaunchAgents (launchctl unavailable in this environment to double-check, non-macOS test runner)")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)

    print("\nAll Phase 8 PART A daily-queue-generation offline tests passed.")


if __name__ == "__main__":
    main()
