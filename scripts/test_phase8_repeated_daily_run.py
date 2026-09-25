#!/usr/bin/env python3

"""
Phase 8 PART C -- offline repeated-daily-run idempotency test
(data/reports/phase8_multisource_validation.md). Runs the FULL cycle
(submit_search.py --confirm -> claim/process -> generate_run_report.py)
TWICE against one temp DB, using a deterministic fake adapter (no
network), then simulates a "next day" by processing the resulting
second submission and confirming fresh work is still generatable.

SAFETY: this file follows the exact same mandatory pattern as
scripts/test_phase8_daily_queue.py -- every submission explicitly
restricts `sources` to the fake adapter registered here. See that
file's own safety note (and data/reports/phase8_multisource_validation.md's
disclosed-deviation section) for exactly why this is mandatory: the
real, shared source_registry has NaukriAdapter genuinely ENABLED, and
omitting --source would queue (and, once processed, actually execute)
a real live Naukri query.

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
import generate_run_report as grr

SUBMIT_CLI = ROOT / "scripts" / "submit_search.py"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python3"
PYTHON_FOR_CLI = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

_RICH_JD = (
    "Senior Site Reliability Engineer / DevOps role. Own production reliability "
    "across AWS and Azure. Run Kubernetes (EKS/AKS) clusters, manage "
    "infrastructure as code with Terraform and Ansible, build CI/CD pipelines "
    "with Jenkins and GitHub Actions, and drive incident management, "
    "SLO/SLI/error budget practices, and root cause analysis. Observability "
    "via Prometheus and Grafana is core."
)

_JOB_DAY1 = {
    "source": "FAKE_PHASE8C", "job_id": "DAY1-JOB", "company": "Alpha Corp",
    "title": "Senior Site Reliability Engineer", "location": "Bengaluru", "work_model": "Hybrid",
    "job_url": "https://fake-phase8c.example.com/jobs/day1", "application_url": "https://fake-phase8c.example.com/apply/day1",
    "posted_date": "2026-09-20", "jd_text": _RICH_JD, "experience_required": "8-12 years",
    "mandatory_skills": [], "preferred_skills": [],
}


class FakeAdapterPhase8C(JobSourceAdapter):
    name = "FAKE_PHASE8C"
    status = AdapterStatus.ENABLED
    jobs_to_return = []

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [dict(j) for j in FakeAdapterPhase8C.jobs_to_return]


source_registry.ADAPTERS["FAKE_PHASE8C"] = FakeAdapterPhase8C


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _new_isolated_db():
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_phase8c_"))
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
    now = "2026-09-20T00:00:00+00:00"
    raw = {
        "identity": {"candidate_id": candidate_id, "name": "Phase 8C Candidate"},
        "professional_summary": {"total_experience_years": 11},
        "skills": {
            "cloud": [{"name": "AWS"}, {"name": "Azure"}],
            "containers_orchestration": [{"name": "Kubernetes"}, {"name": "EKS"}, {"name": "AKS"}],
            "infrastructure_iac": [{"name": "Terraform"}, {"name": "Ansible"}],
            "cicd": [{"name": "Jenkins"}, {"name": "GitHub Actions"}],
            "observability": [{"name": "Prometheus"}, {"name": "Grafana"}],
        },
        "job_preferences": {"target_roles": ["Senior Site Reliability Engineer"], "target_locations": ["Bengaluru"]},
    }
    profile = promote_to_confirmed(normalize_candidate_profile(raw))
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO candidates (candidate_id, name, email, phone, created_at, updated_at, status) VALUES (?,?,?,?,?,?,?)",
        (candidate_id, "Phase 8C Candidate", None, None, now, now, "ACTIVE"),
    )
    conn.execute(
        "INSERT INTO candidate_search_profile (candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) VALUES (?,1,'PROFILE',?,1,1,?,?)",
        (candidate_id, json.dumps(serialize_candidate_profile(profile)), now, now),
    )
    conn.commit()
    conn.close()


def _submit(db_path, candidate_id):
    args = [PYTHON_FOR_CLI, str(SUBMIT_CLI), "--candidate-id", candidate_id, "--source", "FAKE_PHASE8C",
            "--max-job-age-days", "3", "--confirm", "--db", str(db_path)]
    return subprocess.run(args, capture_output=True, text=True, timeout=30)


def _process_one(db_path, candidate_id):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    claimed = claim_next_queue_item(conn, candidate_id=candidate_id)
    result = process_queue_item(conn, claimed) if claimed else None
    conn.close()
    return result


def _counts(db_path):
    conn = sqlite3.connect(db_path)
    runs = conn.execute("SELECT COUNT(*) FROM search_runs").fetchone()[0]
    queue = conn.execute("SELECT COUNT(*) FROM search_queue").fetchone()[0]
    conn.close()
    return runs, queue


def main():
    failures = []
    print("PHASE 8 PART C -- REPEATED DAILY RUN IDEMPOTENCY TEST (temp DB only)")
    print("=================================================================================")

    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-8c")

    # === RUN 1: search work is created, "Naukri"(fake) search executes, report generated ===
    FakeAdapterPhase8C.jobs_to_return = [_JOB_DAY1]
    submit1 = _submit(db, "cand-8c")
    if submit1.returncode != 0 or "SUBMITTED" not in submit1.stdout:
        _fail(failures, f"RUN 1: submission failed: {submit1.stdout} {submit1.stderr}")
    result1 = _process_one(db, "cand-8c")
    if result1 is None or result1.status != "COMPLETED":
        _fail(failures, f"RUN 1: processing failed: {result1.status if result1 else None}")
    else:
        print(f"PASS: RUN 1 -> search work created, fake-source search executed (raw={result1.raw_count}, matches={result1.matches_created})")

    out1 = Path(tempfile.mkdtemp()) / "run1_report.xlsx"
    report1 = grr.generate(str(db), "cand-8c", out1, since="2026-09-20T00:00:00")
    if not out1.exists():
        _fail(failures, "RUN 1: report was not generated")
    else:
        print(f"PASS: RUN 1 -> report generated ({out1.stat().st_size} bytes), APPLY_TODAY={report1['summary']['apply_today_count']}")

    runs_after1, queue_after1 = _counts(db)

    # === RUN 2 (same day, immediately after): today's work is not
    # duplicated unnecessarily. Since RUN 1's item already COMPLETED,
    # a second submission this same "day" creates a fresh item (this
    # is the correct, intended, existing behavior -- NOT a bug: see
    # test 4 in test_phase8_daily_queue.py and this file's own
    # docstring). What must NOT happen is an uncontrolled explosion:
    # calling submit twice in a row (no processing in between) must
    # still be deduplicated by the fingerprint check. Verify THAT
    # specific property here, then process the single resulting item.
    submit2a = _submit(db, "cand-8c")
    submit2b = _submit(db, "cand-8c")  # immediately again, before processing
    runs_mid, queue_mid = _counts(db)
    if runs_mid != runs_after1 + 1 or queue_mid != queue_after1 + 1:
        _fail(failures, f"RUN 2 prep: expected exactly ONE new row from two back-to-back submissions (fingerprint dedup), got runs {runs_after1}->{runs_mid}, queue {queue_after1}->{queue_mid}")
    else:
        print("PASS: RUN 2 prep -> two back-to-back same-day submissions before processing produce exactly ONE new queue item (no duplicate explosion)")

    result2 = _process_one(db, "cand-8c")
    if result2 is None or result2.status != "COMPLETED":
        _fail(failures, f"RUN 2: processing failed: {result2.status if result2 else None}")
    else:
        print(f"PASS: RUN 2 -> processed deterministically (raw={result2.raw_count}, matches_updated={result2.matches_updated})")

    out2 = Path(tempfile.mkdtemp()) / "run2_report.xlsx"
    report2 = grr.generate(str(db), "cand-8c", out2, since="2026-09-20T00:00:00")
    if not out2.exists():
        _fail(failures, "RUN 2: report was not generated")
    elif out1.exists() and out1.read_bytes() == b"":
        _fail(failures, "RUN 2: RUN 1's report was corrupted (empty)")
    else:
        print(f"PASS: RUN 2 -> new report generated without corrupting RUN 1's report; RUN 1's report still exists and is non-empty ({out1.stat().st_size} bytes)")

    # The same job (DAY1-JOB) rediscovered in RUN 2 must show as
    # previously-seen, not duplicated in the job inventory.
    conn = sqlite3.connect(db)
    job_row_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE job_url = ?", (_JOB_DAY1["job_url"],)).fetchone()[0]
    conn.close()
    if job_row_count != 1:
        _fail(failures, f"RUN 2: expected exactly 1 global jobs row for the rediscovered job (no duplicate row), got {job_row_count}")
    else:
        print("PASS: RUN 2 -> the same rediscovered job produced exactly 1 jobs row, not a duplicate")

    # === Simulate the next day: a job posted "today" (relative to a
    # later `since` cutoff) is genuinely new work being generated ===
    _JOB_DAY2 = dict(_JOB_DAY1)
    _JOB_DAY2["job_id"] = "DAY2-JOB"
    _JOB_DAY2["job_url"] = "https://fake-phase8c.example.com/jobs/day2"
    _JOB_DAY2["application_url"] = "https://fake-phase8c.example.com/apply/day2"
    _JOB_DAY2["company"] = "Beta Corp"
    FakeAdapterPhase8C.jobs_to_return = [_JOB_DAY1, _JOB_DAY2]

    submit3 = _submit(db, "cand-8c")
    if submit3.returncode != 0 or "SUBMITTED" not in submit3.stdout:
        _fail(failures, f"next-day simulation: submission failed: {submit3.stdout} {submit3.stderr}")
    result3 = _process_one(db, "cand-8c")
    if result3 is None or result3.status != "COMPLETED":
        _fail(failures, f"next-day simulation: processing failed: {result3.status if result3 else None}")
    else:
        conn = sqlite3.connect(db)
        job2_row = conn.execute("SELECT COUNT(*) FROM jobs WHERE company = 'Beta Corp'").fetchone()[0]
        conn.close()
        if job2_row != 1:
            _fail(failures, f"next-day simulation: expected the new Beta Corp job to appear exactly once, got {job2_row}")
        else:
            print("PASS: next-day simulation -> a fresh submission after prior work completed correctly discovers and persists NEW work (Beta Corp job)")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)

    print("\nAll Phase 8 PART C repeated-daily-run tests passed.")


if __name__ == "__main__":
    main()
