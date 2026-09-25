#!/usr/bin/env python3

"""
Integration tests confirming every job preparation/ingestion path in
this project routes through the single shared
job_eligibility.assess_job_eligibility() gate, and cannot accidentally
bypass it.

Covers: prepare_jobs.py (zero-write dry-run), ingest_job.py,
ingest_jobs.py, discover_and_ingest.py (the three real DB-writing
ingestion paths found during code inspection -- confirmed via
`grep -rn "score_job(" scripts/*.py` that no other production script
calls score_job() directly).

Isolation: ingest_job.py/ingest_jobs.py/discover_and_ingest.py write to
SQLite, so this file redirects tracker.DB_PATH and filter_jobs.DB_PATH
to a fresh temporary database (via init_tracker.main(), unmodified
schema logic) before importing/calling any of them -- it never opens
data/applications/jobos.db. prepare_jobs.py's dry-run functions read
(but never write) the tracker DB via filter_jobs, so the same
redirection covers them too.
"""

import inspect
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import init_tracker
import tracker
import filter_jobs


def _use_isolated_db(prefix):
    tmp_dir = tempfile.mkdtemp(prefix=prefix)
    tmp_db_path = Path(tmp_dir) / "jobos_test.db"

    init_tracker.DATA_DIR = Path(tmp_dir)
    init_tracker.DB_PATH = tmp_db_path
    init_tracker.main()

    tracker.DB_PATH = tmp_db_path
    filter_jobs.DB_PATH = tmp_db_path

    return Path(tmp_dir)


_use_isolated_db("jobos_test_job_eligibility_integration_")

import prepare_jobs
import ingest_job
import ingest_jobs
import discover_and_ingest


BELOW_PROFILE_JOB_TEMPLATE = {
    "source": "TEST",
    "company": "Example Co",
    "title": "Senior Site Reliability Engineer",
    "location": "Bengaluru",
    "work_model": "Hybrid",
    "experience_required": "1-3 years",
    "jd_text": (
        "SRE AWS Kubernetes Terraform Jenkins Prometheus SLO incident "
        "reliability production cloud"
    ),
    "mandatory_skills": [],
    "preferred_skills": [],
}

ELIGIBLE_JOB_TEMPLATE = {
    "source": "TEST",
    "company": "Example Co",
    "title": "Senior Site Reliability Engineer",
    "location": "Bengaluru",
    "work_model": "Hybrid",
    "experience_required": "10-15 years",
    "jd_text": (
        "SRE AWS Azure Kubernetes EKS AKS Terraform Jenkins GitHub "
        "Actions Prometheus Grafana SLO SLI incident reliability "
        "production support root cause RCA cloud platform "
        "microservices infrastructure distributed systems"
    ),
    "mandatory_skills": [],
    "preferred_skills": [],
}

from score_job import PROFILE


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _row_count(job_id):
    conn = tracker.connect()
    row = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    conn.close()
    return row[0]


def test_b1_prepare_jobs_uses_shared_component():
    """
    Static check: prepare_jobs.py imports and calls
    job_eligibility.assess_job_eligibility() rather than re-implementing
    or independently re-calling assess_experience_eligibility()/
    assess_location_eligibility() itself.
    """
    failures = []
    source = inspect.getsource(prepare_jobs)

    if "from job_eligibility import assess_job_eligibility" not in source:
        _fail(failures, "B1: prepare_jobs.py does not import assess_job_eligibility from job_eligibility")
    if "eligibility_result = assess_job_eligibility(" not in source:
        _fail(failures, "B1: prepare_jobs.py does not call assess_job_eligibility() to produce its eligibility decision")
    if "from experience_eligibility import" in source or "from location_taxonomy import" in source:
        _fail(
            failures,
            "B1: prepare_jobs.py still imports experience_eligibility/"
            "location_taxonomy directly instead of going through the "
            "shared job_eligibility component",
        )

    if not failures:
        print("PASS: B1 -> prepare_jobs.py imports and uses job_eligibility.assess_job_eligibility() exclusively")

    return failures


def test_b1b_prepare_jobs_behaviorally_excludes_below_profile():
    failures = []

    raw_jobs = [dict(BELOW_PROFILE_JOB_TEMPLATE, job_id="INTEG-PREP-BELOW-001")]
    result = prepare_jobs._prepare_from_raw_jobs(raw_jobs, PROFILE)

    prepared_ids = [item["job"]["job_id"] for item in result["prepared"]]
    excluded_ids = [item["job"]["job_id"] for item in result["excluded_experience"]]

    if "INTEG-PREP-BELOW-001" in prepared_ids:
        _fail(failures, "B1b: BELOW_PROFILE job reached prepare_jobs' scored 'prepared' list")
    elif "INTEG-PREP-BELOW-001" not in excluded_ids:
        _fail(failures, "B1b: BELOW_PROFILE job missing from excluded_experience entirely (silently dropped)")
    else:
        print("PASS: B1b -> prepare_jobs.py behaviorally excludes a BELOW_PROFILE job via the shared gate")

    return failures


def test_b2_ingest_job_cannot_bypass_eligibility():
    failures = []
    tmp_dir = _use_isolated_db("jobos_test_ingest_job_")

    job = dict(BELOW_PROFILE_JOB_TEMPLATE, job_id="INTEG-INGEST-JOB-BELOW-001")
    job_path = tmp_dir / "job.json"
    job_path.write_text(json.dumps(job))

    old_argv = sys.argv
    try:
        sys.argv = ["ingest_job.py", str(job_path)]
        ingest_job.main()
    finally:
        sys.argv = old_argv

    count = _row_count("INTEG-INGEST-JOB-BELOW-001")

    if count != 0:
        _fail(
            failures,
            f"B2: ingest_job.py wrote a BELOW_PROFILE job to the tracker "
            f"DB (row count={count}) -- it bypassed the eligibility gate",
        )
    else:
        print("PASS: B2 -> ingest_job.py refuses to write a BELOW_PROFILE job (0 rows)")

    return failures


def test_b2b_ingest_job_still_ingests_eligible_job():
    failures = []
    tmp_dir = _use_isolated_db("jobos_test_ingest_job_eligible_")

    job = dict(ELIGIBLE_JOB_TEMPLATE, job_id="INTEG-INGEST-JOB-ELIGIBLE-001")
    job_path = tmp_dir / "job.json"
    job_path.write_text(json.dumps(job))

    old_argv = sys.argv
    try:
        sys.argv = ["ingest_job.py", str(job_path)]
        ingest_job.main()
    finally:
        sys.argv = old_argv

    count = _row_count("INTEG-INGEST-JOB-ELIGIBLE-001")

    if count != 1:
        _fail(failures, f"B2b: expected an eligible job to still be ingested (row count=1), got {count}")
    else:
        print("PASS: B2b -> ingest_job.py still ingests an eligible job normally (backward compatible)")

    return failures


def test_b3_ingest_jobs_cannot_bypass_eligibility():
    failures = []
    tmp_dir = _use_isolated_db("jobos_test_ingest_jobs_")

    jobs = [
        dict(BELOW_PROFILE_JOB_TEMPLATE, job_id="INTEG-INGEST-JOBS-BELOW-001"),
        dict(ELIGIBLE_JOB_TEMPLATE, job_id="INTEG-INGEST-JOBS-ELIGIBLE-001"),
    ]
    batch_path = tmp_dir / "batch.json"
    batch_path.write_text(json.dumps(jobs))

    old_argv = sys.argv
    try:
        sys.argv = ["ingest_jobs.py", str(batch_path)]
        ingest_jobs.main()
    finally:
        sys.argv = old_argv

    below_count = _row_count("INTEG-INGEST-JOBS-BELOW-001")
    eligible_count = _row_count("INTEG-INGEST-JOBS-ELIGIBLE-001")

    if below_count != 0:
        _fail(failures, f"B3: ingest_jobs.py wrote a BELOW_PROFILE job to the tracker DB (row count={below_count})")
    if eligible_count != 1:
        _fail(failures, f"B3: expected the eligible job in the same batch to still be ingested, got row count={eligible_count}")

    if not failures:
        print("PASS: B3 -> ingest_jobs.py excludes the BELOW_PROFILE job and still ingests the eligible one in the same batch")

    return failures


def test_b4_discover_and_ingest_cannot_bypass_eligibility():
    failures = []
    tmp_dir = _use_isolated_db("jobos_test_discover_and_ingest_")

    jobs = [
        dict(BELOW_PROFILE_JOB_TEMPLATE, job_id="INTEG-DISCOVER-BELOW-001"),
        dict(ELIGIBLE_JOB_TEMPLATE, job_id="INTEG-DISCOVER-ELIGIBLE-001"),
    ]
    batch_path = tmp_dir / "batch.json"
    batch_path.write_text(json.dumps(jobs))

    old_argv = sys.argv
    try:
        sys.argv = ["discover_and_ingest.py", str(batch_path)]
        discover_and_ingest.main()
    finally:
        sys.argv = old_argv

    below_count = _row_count("INTEG-DISCOVER-BELOW-001")
    eligible_count = _row_count("INTEG-DISCOVER-ELIGIBLE-001")

    if below_count != 0:
        _fail(failures, f"B4: discover_and_ingest.py wrote a BELOW_PROFILE job to the tracker DB (row count={below_count})")
    if eligible_count != 1:
        _fail(failures, f"B4: expected the eligible job in the same batch to still be ingested, got row count={eligible_count}")

    if not failures:
        print("PASS: B4 -> discover_and_ingest.py excludes the BELOW_PROFILE job and still ingests the eligible one")

    return failures


def test_b5_no_other_production_caller_bypasses_the_gate():
    """
    Static, repo-wide check mirroring the grep performed during
    inspection: every non-test .py file that calls score_job() directly
    must be one of the four already-verified callers (prepare_jobs.py,
    ingest_job.py, ingest_jobs.py, discover_and_ingest.py), or
    score_job.py itself (its own CLI entry point, which has no
    tracker-writing side effect). If a new ingestion script is added
    later and calls score_job() without going through
    assess_job_eligibility() first, this test is designed to catch it.
    """
    failures = []

    known_callers = {
        "score_job.py",
        "prepare_jobs.py",
        "ingest_job.py",
        "ingest_jobs.py",
        "discover_and_ingest.py",
        # search_worker.py: manually reviewed (see
        # test_search_worker.py's test_26_eligibility_before_scoring)
        # -- it calls assess_job_eligibility() and only calls
        # score_job() for jobs already found eligible.
        "search_worker.py",
        # job_ranking.py: manually reviewed (see
        # test_job_ranking.py's test_ineligible_job_never_calls_score_job)
        # -- it calls assess_job_eligibility() and only calls score_job()
        # for jobs already found eligible, exactly mirroring
        # search_worker.py's own pattern above.
        "job_ranking.py",
    }

    # Matches an actual invocation with a `job` argument (score_job(job
    # or score_job(\n    job, ...) -- never a bare "score_job()" prose
    # mention such as "the same convention score_job() uses", which
    # several docstrings in this codebase legitimately contain.
    call_pattern = re.compile(r"score_job\(\s*job\b")

    scripts_dir = ROOT / "scripts"
    offending = []

    for path in sorted(scripts_dir.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        if path.name in known_callers:
            continue

        text = path.read_text(encoding="utf-8")
        if call_pattern.search(text):
            offending.append(path.name)

    if offending:
        _fail(
            failures,
            f"B5: found non-test script(s) calling score_job() directly "
            f"that are not among the known, eligibility-gated callers: "
            f"{offending} -- these must be routed through "
            f"job_eligibility.assess_job_eligibility() or added to this "
            f"test's known_callers set after manual review",
        )
    else:
        print(
            f"PASS: B5 -> no undiscovered production script calls "
            f"score_job() directly outside the {len(known_callers)} "
            f"known, eligibility-gated callers"
        )

    return failures


def main():
    tests = [
        test_b1_prepare_jobs_uses_shared_component,
        test_b1b_prepare_jobs_behaviorally_excludes_below_profile,
        test_b2_ingest_job_cannot_bypass_eligibility,
        test_b2b_ingest_job_still_ingests_eligible_job,
        test_b3_ingest_jobs_cannot_bypass_eligibility,
        test_b4_discover_and_ingest_cannot_bypass_eligibility,
        test_b5_no_other_production_caller_bypasses_the_gate,
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

    print(f"All {len(tests)} job-eligibility integration tests passed.")
    print(
        "Note: this test file never opened data/applications/jobos.db -- "
        "every ingestion path was exercised against its own isolated "
        "temporary SQLite database."
    )


if __name__ == "__main__":
    main()
