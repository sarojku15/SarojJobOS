#!/usr/bin/env python3

"""
Phase 1/2 regression test: search_run_sources persists one row per
requested source, with attempted/reachable/succeeded/raw_count/
eligible_count/displayed_count/status/error_type/error_message/
duration_ms all correctly recorded -- including for a source that
returns zero results, one that is blocked, one whose query fails, and
one that isn't configured at all. One source's failure never prevents
another source from completing (a fresh process_queue_item() call, no
special-casing).

Fully offline: no real network call anywhere in this file. Never opens
data/applications/jobos.db.
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
import migrate_v5_search_run_sources
import source_registry
from source_adapter import (
    JobSourceAdapter, AdapterHealth, AdapterStatus, BlockReason,
    AdapterBlockedError, AdapterTimeoutError,
)
from candidate_profile import normalize_candidate_profile, promote_to_confirmed, serialize_candidate_profile
from search_submission import submit_search
from search_worker import claim_next_queue_item, process_queue_item

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"

_RICH_JD = (
    "Senior Site Reliability Engineer / DevOps role. Own production reliability "
    "across AWS and Azure. Run Kubernetes (EKS/AKS) clusters, manage "
    "infrastructure as code with Terraform and Ansible, build CI/CD pipelines "
    "with Jenkins and GitHub Actions, and drive incident management, "
    "SLO/SLI/error budget practices, and root cause analysis. Observability "
    "via Prometheus and Grafana is core."
)


def _job(source, i):
    return {
        "source": source, "job_id": f"{source}-JOB-{i}", "company": f"{source} Corp",
        "title": "Senior Site Reliability Engineer", "location": "Bengaluru", "work_model": "Hybrid",
        "job_url": f"https://{source.lower()}.example.com/jobs/{i}",
        "application_url": f"https://{source.lower()}.example.com/apply/{i}",
        "posted_date": "2026-09-20", "jd_text": _RICH_JD, "experience_required": "8-12 years",
        "mandatory_skills": [], "preferred_skills": [],
    }


class _SuccessAdapter(JobSourceAdapter):
    name = "FAKE_SRS_SUCCESS"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [_job(self.name, 1), _job(self.name, 2)]


class _ZeroAdapter(JobSourceAdapter):
    name = "FAKE_SRS_ZERO"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return []


class _BlockedAdapter(JobSourceAdapter):
    name = "FAKE_SRS_BLOCKED"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        raise AdapterBlockedError(self.name, BlockReason.CAPTCHA, detail="simulated CAPTCHA")


class _FailedAdapter(JobSourceAdapter):
    name = "FAKE_SRS_FAILED"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        raise AdapterTimeoutError(self.name, detail="simulated timeout")


class _NotConfiguredAdapter(JobSourceAdapter):
    name = "FAKE_SRS_NOT_CONFIGURED"
    status = AdapterStatus.NOT_ENABLED

    def health_check(self):
        raise AssertionError("NOT_ENABLED adapter's health_check() must never be called")

    def search(self, query):
        raise AssertionError("NOT_ENABLED adapter's search() must never be called")


_FAKE_SOURCES = ["FAKE_SRS_SUCCESS", "FAKE_SRS_ZERO", "FAKE_SRS_BLOCKED", "FAKE_SRS_FAILED", "FAKE_SRS_NOT_CONFIGURED"]

source_registry.ADAPTERS["FAKE_SRS_SUCCESS"] = _SuccessAdapter
source_registry.ADAPTERS["FAKE_SRS_ZERO"] = _ZeroAdapter
source_registry.ADAPTERS["FAKE_SRS_BLOCKED"] = _BlockedAdapter
source_registry.ADAPTERS["FAKE_SRS_FAILED"] = _FailedAdapter
source_registry.ADAPTERS["FAKE_SRS_NOT_CONFIGURED"] = _NotConfiguredAdapter


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _sha(path):
    import hashlib
    if not Path(path).exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _new_isolated_db():
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_srs_"))
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

    migrate_v5_search_run_sources.migrate(tmp_db)
    return tmp_db


def _seed_confirmed_candidate(db_path, candidate_id):
    now = "2026-09-20T00:00:00+00:00"
    raw = {
        "identity": {"candidate_id": candidate_id, "name": "Test Candidate"},
        "professional_summary": {"total_experience_years": 11.0},
        "skills": {
            "cloud": [{"name": "AWS"}, {"name": "Azure"}],
            "containers_orchestration": [{"name": "Kubernetes"}],
            "infrastructure_iac": [{"name": "Terraform"}],
            "cicd": [{"name": "Jenkins"}],
            "observability": [{"name": "Prometheus"}, {"name": "Grafana"}],
        },
        "job_preferences": {"target_roles": ["Senior SRE"], "target_locations": ["Bengaluru"]},
    }
    profile = promote_to_confirmed(normalize_candidate_profile(raw))
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO candidates (candidate_id, name, email, phone, created_at, updated_at, status) VALUES (?,?,?,?,?,?,?)",
        (candidate_id, "Test Candidate", None, None, now, now, "ACTIVE"),
    )
    conn.execute(
        "INSERT INTO candidate_search_profile (candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) VALUES (?,1,'PROFILE',?,1,1,?,?)",
        (candidate_id, json.dumps(serialize_candidate_profile(profile)), now, now),
    )
    conn.commit()
    conn.close()


def main():
    failures = []
    print("SEARCH_RUN_SOURCES PER-SOURCE EXECUTION AUDIT TEST")
    print("=================================================================================")

    production_before = _sha(PRODUCTION_DB)

    db = _new_isolated_db()
    candidate_id = "cand-srs"
    _seed_confirmed_candidate(db, candidate_id)

    submission = submit_search(db_path=str(db), candidate_id=candidate_id, sources=_FAKE_SOURCES, dry_run=False)
    if not submission.submitted:
        _fail(failures, f"submission failed: {submission}")

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    claimed = claim_next_queue_item(conn, candidate_id=candidate_id)
    if claimed is None:
        _fail(failures, "expected a claimable queue item")
        conn.close()
        print("\n".join(failures))
        sys.exit(1)

    work_result = process_queue_item(conn, claimed)
    conn.close()

    # One source failing (BLOCKED/FAILED) must never prevent the
    # others from completing.
    if work_result.status not in ("PARTIAL", "COMPLETED"):
        _fail(failures, f"expected PARTIAL (mixed success/failure across sources), got {work_result.status}")
    else:
        print(f"PASS: 1. queue item finished with status={work_result.status} despite 2 of 5 sources failing -- one source's failure did not stop the others")

    conn2 = sqlite3.connect(db)
    conn2.row_factory = sqlite3.Row
    rows = {
        r["source"]: dict(r)
        for r in conn2.execute(
            "SELECT * FROM search_run_sources WHERE search_run_id = ?", (submission.search_run_id,)
        ).fetchall()
    }
    conn2.close()

    if set(rows) != set(_FAKE_SOURCES):
        _fail(failures, f"2. expected a row for all 5 requested sources, got {sorted(rows)}")
    else:
        print(f"PASS: 2. exactly one search_run_sources row persisted per requested source: {sorted(rows)}")

    expectations = {
        "FAKE_SRS_SUCCESS": {"status": "SUCCESS", "attempted": 1, "raw_count": 2, "eligible_count": 2, "displayed_count": 2},
        "FAKE_SRS_ZERO": {"status": "ZERO", "attempted": 1, "raw_count": 0, "eligible_count": 0, "displayed_count": 0},
        "FAKE_SRS_BLOCKED": {"status": "BLOCKED", "attempted": 1, "raw_count": 0},
        "FAKE_SRS_FAILED": {"status": "FAILED", "attempted": 1, "raw_count": 0},
        "FAKE_SRS_NOT_CONFIGURED": {"status": "NOT_CONFIGURED", "attempted": 0, "raw_count": 0},
    }
    for source, expected in expectations.items():
        row = rows.get(source)
        if row is None:
            continue
        for field, expected_value in expected.items():
            if row[field] != expected_value:
                _fail(failures, f"3. {source}.{field}: expected {expected_value}, got {row[field]}")

    if not any(f.startswith("3.") for f in failures):
        print("PASS: 3. every source's status/attempted/raw_count/eligible_count/displayed_count exactly matches its real outcome (SUCCESS/ZERO/BLOCKED/FAILED/NOT_CONFIGURED all distinguished, never collapsed)")

    # Zero-result and failed/blocked sources are PERSISTED, not silently dropped.
    if rows.get("FAKE_SRS_ZERO", {}).get("attempted") != 1:
        _fail(failures, "4. zero-result source must be persisted with attempted=1, proving it WAS searched")
    else:
        print("PASS: 4. zero-result source persisted with attempted=1 -- distinguishable from never-searched")

    if rows.get("FAKE_SRS_BLOCKED", {}).get("error_type") != "CAPTCHA":
        _fail(failures, f"5. blocked source should carry error_type='CAPTCHA', got {rows.get('FAKE_SRS_BLOCKED', {}).get('error_type')}")
    else:
        print("PASS: 5. blocked source's error_type correctly reflects the real BlockReason (CAPTCHA)")

    if rows.get("FAKE_SRS_FAILED", {}).get("error_type") != "QUERY_FAILED":
        _fail(failures, f"6. failed source should carry error_type='QUERY_FAILED', got {rows.get('FAKE_SRS_FAILED', {}).get('error_type')}")
    else:
        print("PASS: 6. failed (timed-out) source's error_type correctly recorded")

    # Duration: the successful source made a real (fake, instant) call,
    # so it should have both timestamps and a duration >= 0.
    success_row = rows.get("FAKE_SRS_SUCCESS", {})
    if success_row.get("started_at") is None or success_row.get("completed_at") is None:
        _fail(failures, "7. SUCCESS source should have both started_at and completed_at timestamps")
    elif success_row.get("duration_ms") is None or success_row["duration_ms"] < 0:
        _fail(failures, f"7. SUCCESS source should have a non-negative duration_ms, got {success_row.get('duration_ms')}")
    else:
        print(f"PASS: 7. SUCCESS source has real per-source timing (duration_ms={success_row['duration_ms']})")

    not_configured_row = rows.get("FAKE_SRS_NOT_CONFIGURED", {})
    if not_configured_row.get("started_at") is not None:
        _fail(failures, "8. NOT_CONFIGURED source (never actually run) should have no started_at timestamp")
    else:
        print("PASS: 8. NOT_CONFIGURED source correctly has no execution timing (it never ran)")

    # Re-running the SAME queue item's audit persistence must not
    # duplicate rows (idempotent via the UNIQUE(search_run_id, source) upsert).
    conn3 = sqlite3.connect(db)
    conn3.execute("PRAGMA foreign_keys = ON")
    from search_worker import _persist_search_run_sources
    _persist_search_run_sources(conn3, submission.search_run_id, [(s, None) for s in _FAKE_SOURCES], [], [], [])
    count_after = conn3.execute(
        "SELECT COUNT(*) FROM search_run_sources WHERE search_run_id = ?", (submission.search_run_id,)
    ).fetchone()[0]
    conn3.close()
    if count_after != 5:
        _fail(failures, f"9. re-persisting must UPSERT, not duplicate -- expected 5 rows, got {count_after}")
    else:
        print("PASS: 9. re-persisting the same search_run_id/source pairs upserts, never duplicates")

    production_after = _sha(PRODUCTION_DB)
    if production_before != production_after:
        _fail(failures, "production DB was touched -- SAFETY VIOLATION")
    else:
        print("PASS: production DB untouched (SHA unchanged)")

    print()
    if failures:
        print(f"TOTAL: {len(failures)} failure(s)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL search_run_sources AUDIT CHECKS PASSED")


if __name__ == "__main__":
    main()
