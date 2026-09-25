#!/usr/bin/env python3

"""
Temp-DB-only integration test for the NEW in-memory ranking enrichment
added to search_worker.py's process_queue_item() (RankingRecord
construction via job_ranking.build_ranking_record(), plus cross-source
duplicate-candidate detection via cross_source_dedup.py). Never opens
data/applications/jobos.db, never makes a network/browser call --
sources are deterministic, test-local fake adapters registered into
source_registry.ADAPTERS, exactly matching test_search_worker.py's own
established FakeAdapter precedent (duplicated here deliberately, per
this project's convention of self-contained test files -- see that
file's own _new_isolated_db()/_seed_confirmed_candidate() for the
identical pattern).
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import init_tracker
import migrate_v2_schema
import source_registry
from source_adapter import (
    JobSourceAdapter,
    SearchQuery,
    AdapterHealth,
    BlockReason,
    AdapterBlockedError,
    AdapterTimeoutError,
)
from candidate_profile import normalize_candidate_profile, promote_to_confirmed, serialize_candidate_profile
from search_submission import submit_search
from search_worker import claim_next_queue_item, process_queue_item
from score_job import score_job as real_score_job
from job_eligibility import assess_job_eligibility


# ----------------------------------------------------------------------
# Job fixtures (6 jobs, per this task's exact required scenario)
# ----------------------------------------------------------------------

JOB_PERFECT_MATCH = {
    "source": "FAKE_SOURCE_A",
    "job_id": "A-PERFECT-001",
    "company": "Example Technologies",
    "title": "Senior Site Reliability Engineer",
    "location": "Bengaluru",
    "work_model": "Hybrid",
    "job_url": "https://fake-a.example.com/jobs/1",
    "application_url": "",
    "posted_date": "2026-09-19",
    "jd_text": (
        "We are looking for a Senior Site Reliability Engineer / DevOps engineer to own "
        "production reliability across AWS and Azure. You will run Kubernetes (EKS/AKS) "
        "clusters, manage infrastructure as code with Terraform and Ansible, build CI/CD "
        "pipelines with Jenkins and GitHub Actions, and drive incident management, "
        "SLO/SLI/error budget practices, and root cause analysis. Observability via "
        "Prometheus and Grafana is core. You will work across our cloud platform and "
        "distributed systems, supporting production infrastructure at scale."
    ),
    "experience_required": "8-12 years",
    "mandatory_skills": [],
    "preferred_skills": [],
}

JOB_AZURE_AWS_MATCH = {
    "source": "FAKE_SOURCE_A",
    "job_id": "A-AZUREAWS-002",
    "company": "Northwind Cloud",
    "title": "Senior SRE",
    "location": "Bengaluru",
    "work_model": "",
    "job_url": "https://fake-a.example.com/jobs/2",
    "application_url": "",
    "posted_date": "2026-09-18",
    "jd_text": (
        "Own reliability engineering across AWS and Azure. You will run Kubernetes "
        "clusters, manage Terraform-based infrastructure, and drive incident management, "
        "SLO/SLI/error budget practices, and root cause analysis. Observability via "
        "Prometheus and Grafana."
    ),
    "experience_required": "8-12 years",
    "mandatory_skills": [],
    "preferred_skills": [],
}

JOB_WRONG_LOCATION = {
    "source": "FAKE_SOURCE_A",
    "job_id": "A-WRONGLOC-003",
    "company": "Coastal Systems",
    "title": "Senior Site Reliability Engineer",
    "location": "Mumbai",
    "work_model": "",
    "job_url": "https://fake-a.example.com/jobs/3",
    "application_url": "",
    "posted_date": "2026-09-17",
    "jd_text": "Senior SRE role based in Mumbai, on AWS and Kubernetes.",
    "experience_required": "8-12 years",
    "mandatory_skills": [],
    "preferred_skills": [],
}

JOB_INSUFFICIENT_EXPERIENCE = {
    "source": "FAKE_SOURCE_A",
    "job_id": "A-LOWEXP-004",
    "company": "Fresh Grad Startups",
    "title": "Site Reliability Engineer",
    "location": "Bengaluru",
    "work_model": "",
    "job_url": "https://fake-a.example.com/jobs/4",
    "application_url": "",
    "posted_date": "2026-09-16",
    "jd_text": "Entry-level SRE role supporting our small production environment.",
    "experience_required": "1-3 years",
    "mandatory_skills": [],
    "preferred_skills": [],
}

JOB_IRRELEVANT = {
    "source": "FAKE_SOURCE_A",
    "job_id": "A-IRRELEVANT-005",
    "company": "Willowbrook Foods",
    "title": "Regional Sales Manager",
    "location": "Bengaluru",
    "work_model": "",
    "job_url": "https://fake-a.example.com/jobs/5",
    "application_url": "",
    "posted_date": "2026-09-15",
    "jd_text": "Willowbrook Foods is hiring a Regional Sales Manager to grow our FMCG distribution network.",
    "experience_required": "8-12 years",
    "mandatory_skills": [],
    "preferred_skills": [],
}

JOB_DUPLICATE_OF_PERFECT = {
    "source": "FAKE_SOURCE_B",
    "job_id": "B-MIRROR-001",
    "company": "Example Technologies",
    "title": "Senior Site Reliability Engineer",
    "location": "Bengaluru",
    "work_model": "Hybrid",
    "job_url": "https://fake-b.example.com/careers/1",
    "application_url": "",
    "posted_date": "2026-09-19",
    "jd_text": JOB_PERFECT_MATCH["jd_text"],
    "experience_required": "8-12 years",
    "mandatory_skills": [],
    "preferred_skills": [],
}

SIX_JOBS_SOURCE_A = [JOB_PERFECT_MATCH, JOB_AZURE_AWS_MATCH, JOB_WRONG_LOCATION, JOB_INSUFFICIENT_EXPERIENCE, JOB_IRRELEVANT]
SIX_JOBS_SOURCE_B = [JOB_DUPLICATE_OF_PERFECT]


# ----------------------------------------------------------------------
# Deterministic, configurable fake adapters (no network, ever)
# ----------------------------------------------------------------------

class _Behavior:
    """Mutable, per-test-reset behavior shared by every fake adapter below."""

    jobs_a = []
    jobs_b = []
    raise_blocked_a = False
    raise_timeout_a = False
    raise_generic_a = False
    raise_blocked_b = False

    @classmethod
    def reset(cls):
        cls.jobs_a = []
        cls.jobs_b = []
        cls.raise_blocked_a = False
        cls.raise_timeout_a = False
        cls.raise_generic_a = False
        cls.raise_blocked_b = False


class FakeAdapterA(JobSourceAdapter):
    name = "FAKE_SOURCE_A"

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        if _Behavior.raise_blocked_a:
            raise AdapterBlockedError(self.name, BlockReason.UNKNOWN_BLOCK)
        if _Behavior.raise_timeout_a:
            raise AdapterTimeoutError(self.name, detail="simulated timeout")
        if _Behavior.raise_generic_a:
            raise RuntimeError("simulated parse failure (unhandled exception)")
        return [dict(job) for job in _Behavior.jobs_a]


class FakeAdapterB(JobSourceAdapter):
    name = "FAKE_SOURCE_B"

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        if _Behavior.raise_blocked_b:
            raise AdapterBlockedError(self.name, BlockReason.UNKNOWN_BLOCK)
        return [dict(job) for job in _Behavior.jobs_b]


source_registry.ADAPTERS["FAKE_SOURCE_A"] = FakeAdapterA
source_registry.ADAPTERS["FAKE_SOURCE_B"] = FakeAdapterB


# ----------------------------------------------------------------------
# DB / candidate fixtures (identical pattern to test_search_worker.py)
# ----------------------------------------------------------------------

def _new_isolated_db():
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_ranking_integration_"))
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


def _seed_confirmed_candidate(db_path, candidate_id, target_roles, target_locations, experience_years=11):
    now = "2026-09-20T00:00:00+00:00"
    raw = {
        "identity": {"candidate_id": candidate_id, "name": "Ranking Integration Candidate"},
        "professional_summary": {"total_experience_years": experience_years},
        "skills": {
            "cloud": [{"name": "AWS"}, {"name": "Azure"}],
            "containers_orchestration": [{"name": "Kubernetes"}, {"name": "EKS"}, {"name": "AKS"}],
            "infrastructure_iac": [{"name": "Terraform"}, {"name": "Ansible"}],
            "cicd": [{"name": "Jenkins"}, {"name": "GitHub Actions"}],
            "observability": [{"name": "Prometheus"}, {"name": "Grafana"}],
        },
        "job_preferences": {"target_roles": target_roles, "target_locations": target_locations},
    }
    profile = promote_to_confirmed(normalize_candidate_profile(raw))

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO candidates (candidate_id, name, email, phone, created_at, updated_at, status) VALUES (?,?,?,?,?,?,?)",
        (candidate_id, "Ranking Integration Candidate", None, None, now, now, "ACTIVE"),
    )
    conn.execute(
        "INSERT INTO candidate_search_profile (candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) VALUES (?,1,'PROFILE',?,1,1,?,?)",
        (candidate_id, json.dumps(serialize_candidate_profile(profile)), now, now),
    )
    conn.commit()
    conn.close()


def _submit(db_path, candidate_id, sources):
    return submit_search(str(db_path), candidate_id, sources=sources, dry_run=False)


def _row_counts(db_path):
    conn = sqlite3.connect(db_path)
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("jobs", "candidate_job_matches", "search_runs", "search_queue")
    }
    conn.close()
    return counts


def _process_one(db_path, candidate_id):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    claimed = claim_next_queue_item(conn, candidate_id=candidate_id)
    result = process_queue_item(conn, claimed) if claimed else None
    conn.close()
    return result


def main():
    failures = []

    print("SEARCH WORKER RANKING INTEGRATION TEST (temp DB only)")
    print("=======================================================")

    # ------------------------------------------------------------------
    # 1. Full 6-job scenario
    # ------------------------------------------------------------------
    _Behavior.reset()
    _Behavior.jobs_a = SIX_JOBS_SOURCE_A
    _Behavior.jobs_b = SIX_JOBS_SOURCE_B

    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-6job", ["Senior Site Reliability Engineer"], ["Bengaluru"])
    submission = _submit(db, "cand-6job", ["FAKE_SOURCE_A", "FAKE_SOURCE_B"])
    result = _process_one(db, "cand-6job")

    if result is None:
        failures.append("6-job scenario: no queue item was claimed")
    else:
        if result.status != "COMPLETED":
            failures.append(f"6-job scenario: expected status COMPLETED, got {result.status}")
        else:
            print("PASS: search run completed successfully (queue item claimed, both sources queried)")

        if result.raw_count != 6:
            failures.append(f"6-job scenario: expected raw_count=6, got {result.raw_count}")
        else:
            print("PASS: all 6 raw jobs normalized")

        if result.eligible_count != 4:
            failures.append(f"6-job scenario: expected eligible_count=4 (perfect, azure/aws, irrelevant, duplicate), got {result.eligible_count}")
        else:
            print("PASS: eligibility ran correctly -> 4 eligible, 2 ineligible (wrong-location, insufficient-experience)")

        if result.scored_count != 4:
            failures.append(f"6-job scenario: expected scored_count=4 (score runs only for eligible jobs), got {result.scored_count}")
        else:
            print("PASS: score_job() ran only for eligible jobs (existing eligibility-before-scoring preserved)")

        if result.matches_created != 4:
            failures.append(f"6-job scenario: expected 4 candidate_job_matches created, got {result.matches_created}")
        else:
            print("PASS: candidate matches created exactly for the 4 eligible jobs")

        counts = _row_counts(db)
        if counts["jobs"] != 6:
            failures.append(f"6-job scenario: expected 6 rows in jobs table (nothing merged/deleted), got {counts['jobs']}")
        else:
            print("PASS: all 6 jobs (including both sides of the duplicate pair) persisted as separate jobs rows")

        # --- RankingRecord construction ---
        if len(result.ranking_records) != 6:
            failures.append(f"expected 6 ranking_records (one per unique job, eligible or not), got {len(result.ranking_records)}")
        else:
            print("PASS: RankingRecord constructed for every unique job, eligible and ineligible alike")

        by_id = {r.job_id: r for r in result.ranking_records}

        perfect = by_id.get("A-PERFECT-001")
        mirror = by_id.get("B-MIRROR-001")
        wrong_loc = by_id.get("A-WRONGLOC-003")
        low_exp = by_id.get("A-LOWEXP-004")

        if perfect is None or mirror is None:
            failures.append("could not find expected ranking records for the perfect-match/duplicate pair")
        elif not perfect.duplicate_candidates or not mirror.duplicate_candidates:
            failures.append(
                f"SAFETY VIOLATION: duplicate candidate NOT detected between FAKE_SOURCE_A and FAKE_SOURCE_B "
                f"(perfect.duplicate_candidates={perfect.duplicate_candidates}, mirror.duplicate_candidates={mirror.duplicate_candidates})"
            )
        elif perfect.duplicate_candidates[0]["other_source"] != "FAKE_SOURCE_B" or mirror.duplicate_candidates[0]["other_source"] != "FAKE_SOURCE_A":
            failures.append("duplicate candidate cross-reference points at the wrong source")
        else:
            print(
                f"PASS: duplicate candidate detected between the two sources "
                f"(confidence={perfect.duplicate_candidates[0]['confidence']}) -- neither job was merged or dropped"
            )

        if wrong_loc is None or low_exp is None:
            failures.append("could not find expected ranking records for the ineligible jobs")
        elif wrong_loc.eligible or low_exp.eligible or wrong_loc.score is not None or low_exp.score is not None:
            failures.append("ineligible jobs' ranking records must have eligible=False and score=None")
        else:
            print("PASS: ineligible jobs' RankingRecords correctly show eligible=False, score=None")

        # --- existing score values remain unchanged: cross-check against
        #     calling score_job()/assess_job_eligibility() directly ---
        if perfect is not None:
            from discover_local import normalize_job
            from candidate_profile import to_legacy_matching_profile
            direct_normalized = normalize_job(dict(JOB_PERFECT_MATCH), 1)
            profile_row_conn = sqlite3.connect(db)
            profile_json = profile_row_conn.execute(
                "SELECT profile_json FROM candidate_search_profile WHERE candidate_id = ?", ("cand-6job",)
            ).fetchone()[0]
            profile_row_conn.close()
            from candidate_profile import normalize_candidate_profile as _ncp
            legacy = to_legacy_matching_profile(_ncp(json.loads(profile_json)))
            direct_eligibility = assess_job_eligibility(direct_normalized, legacy)
            direct_scoring = real_score_job(direct_normalized, legacy, experience_assessment=direct_eligibility.experience_assessment)
            if direct_scoring["score"] != perfect.score:
                failures.append(
                    f"SAFETY VIOLATION: worker-produced score ({perfect.score}) differs from a direct "
                    f"score_job() call ({direct_scoring['score']}) on the same job/profile"
                )
            else:
                print(f"PASS: worker-produced score ({perfect.score}) exactly matches a direct score_job() call -- unchanged")

        if not result.ranking_errors:
            print("PASS: no ranking-enrichment errors on the clean 6-job scenario")
        else:
            failures.append(f"unexpected ranking_errors on a clean scenario: {result.ranking_errors}")

    # ------------------------------------------------------------------
    # 2. Zero raw jobs
    # ------------------------------------------------------------------
    _Behavior.reset()
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-zero", ["Senior Site Reliability Engineer"], ["Bengaluru"])
    _submit(db, "cand-zero", ["FAKE_SOURCE_A"])
    result = _process_one(db, "cand-zero")
    if result.status != "COMPLETED" or result.raw_count != 0 or result.ranking_records != []:
        failures.append(f"zero-raw-jobs case misbehaved: status={result.status}, raw_count={result.raw_count}, ranking_records={result.ranking_records}")
    else:
        print("PASS: zero raw jobs -> COMPLETED, no ranking records, no crash")

    # ------------------------------------------------------------------
    # 3. All jobs ineligible
    # ------------------------------------------------------------------
    _Behavior.reset()
    _Behavior.jobs_a = [JOB_WRONG_LOCATION, JOB_INSUFFICIENT_EXPERIENCE]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-allineligible", ["Senior Site Reliability Engineer"], ["Bengaluru"])
    _submit(db, "cand-allineligible", ["FAKE_SOURCE_A"])
    result = _process_one(db, "cand-allineligible")
    if result.status != "COMPLETED" or result.eligible_count != 0 or result.matches_created != 0 or len(result.ranking_records) != 2:
        failures.append(
            f"all-ineligible case misbehaved: status={result.status}, eligible_count={result.eligible_count}, "
            f"matches_created={result.matches_created}, ranking_records={len(result.ranking_records)}"
        )
    elif any(r.eligible for r in result.ranking_records):
        failures.append("all-ineligible case: found a ranking record marked eligible")
    else:
        print("PASS: all-jobs-ineligible -> COMPLETED, 0 matches, 2 ranking records both eligible=False")

    # ------------------------------------------------------------------
    # 4. Adapter blocked
    # ------------------------------------------------------------------
    _Behavior.reset()
    _Behavior.raise_blocked_a = True
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-blocked", ["Senior Site Reliability Engineer"], ["Bengaluru"])
    _submit(db, "cand-blocked", ["FAKE_SOURCE_A"])
    result = _process_one(db, "cand-blocked")
    if result.status != "BLOCKED" or result.ranking_records != [] or result.ranking_errors != []:
        failures.append(f"adapter-blocked case misbehaved: status={result.status}, ranking_records={result.ranking_records}, ranking_errors={result.ranking_errors}")
    else:
        print("PASS: adapter blocked -> status BLOCKED, no ranking records, no ranking errors, no crash")

    # ------------------------------------------------------------------
    # 5. Adapter timeout
    # ------------------------------------------------------------------
    _Behavior.reset()
    _Behavior.raise_timeout_a = True
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-timeout", ["Senior Site Reliability Engineer"], ["Bengaluru"])
    _submit(db, "cand-timeout", ["FAKE_SOURCE_A"])
    result = _process_one(db, "cand-timeout")
    if result.status != "FAILED" or result.ranking_records != []:
        failures.append(f"adapter-timeout case misbehaved: status={result.status}, ranking_records={result.ranking_records}")
    else:
        print("PASS: adapter timeout -> status FAILED, no ranking records, no crash")

    # ------------------------------------------------------------------
    # 6. Parse failure (unhandled generic exception from the adapter)
    # ------------------------------------------------------------------
    _Behavior.reset()
    _Behavior.raise_generic_a = True
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-parsefail", ["Senior Site Reliability Engineer"], ["Bengaluru"])
    _submit(db, "cand-parsefail", ["FAKE_SOURCE_A"])
    result = _process_one(db, "cand-parsefail")
    if result.status != "FAILED" or result.ranking_records != []:
        failures.append(f"parse-failure case misbehaved: status={result.status}, ranking_records={result.ranking_records}")
    else:
        print("PASS: parse failure (unhandled adapter exception) -> status FAILED, no crash, queue/run finalized cleanly")

    # ------------------------------------------------------------------
    # 7. Partial results (one source succeeds, one is blocked)
    # ------------------------------------------------------------------
    _Behavior.reset()
    _Behavior.jobs_a = [JOB_PERFECT_MATCH]
    _Behavior.raise_blocked_b = True
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-partial", ["Senior Site Reliability Engineer"], ["Bengaluru"])
    _submit(db, "cand-partial", ["FAKE_SOURCE_A", "FAKE_SOURCE_B"])
    result = _process_one(db, "cand-partial")
    if result.status != "PARTIAL" or len(result.ranking_records) != 1:
        failures.append(f"partial-results case misbehaved: status={result.status}, ranking_records={len(result.ranking_records)}")
    else:
        print("PASS: partial results (one source blocked, one succeeds) -> status PARTIAL, ranking still runs for the successful source's job")

    # ------------------------------------------------------------------
    # 8. Duplicate source listings (exact intra-source duplicate)
    # ------------------------------------------------------------------
    _Behavior.reset()
    _Behavior.jobs_a = [JOB_PERFECT_MATCH, dict(JOB_PERFECT_MATCH)]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-dup", ["Senior Site Reliability Engineer"], ["Bengaluru"])
    _submit(db, "cand-dup", ["FAKE_SOURCE_A"])
    result = _process_one(db, "cand-dup")
    if result.duplicate_count != 1 or result.unique_count != 1 or len(result.ranking_records) != 1:
        failures.append(
            f"duplicate-source-listing case misbehaved: duplicate_count={result.duplicate_count}, "
            f"unique_count={result.unique_count}, ranking_records={len(result.ranking_records)}"
        )
    else:
        print("PASS: exact intra-source duplicate listing collapsed to 1 (existing discover_local.deduplicate() behavior unchanged)")

    # ------------------------------------------------------------------
    # 9. Scoring/eligibility exception does not corrupt queue/run state
    # ------------------------------------------------------------------
    _Behavior.reset()
    _Behavior.jobs_a = [JOB_PERFECT_MATCH]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-scorefail", ["Senior Site Reliability Engineer"], ["Bengaluru"])
    _submit(db, "cand-scorefail", ["FAKE_SOURCE_A"])

    with mock.patch("search_worker.score_job", side_effect=RuntimeError("simulated scoring failure")):
        result = _process_one(db, "cand-scorefail")

    if result.status != "COMPLETED":
        failures.append(f"scoring-exception case: expected status COMPLETED (query itself succeeded), got {result.status}")
    elif result.matches_created != 0 or result.scored_count != 0:
        failures.append(f"scoring-exception case: expected 0 matches/scored (scoring failed), got matches={result.matches_created}, scored={result.scored_count}")
    elif not result.errors:
        failures.append("scoring-exception case: expected an entry in result.errors describing the scoring failure")
    else:
        print(f"PASS: a scoring exception is caught per-job, does not corrupt queue/run state (status={result.status}), and is recorded in errors")

    counts_after = _row_counts(db)
    if counts_after["jobs"] != 0:
        failures.append(f"scoring-exception case: expected 0 jobs rows (eligible job never got scored so was never upserted), got {counts_after['jobs']}")
    else:
        print("PASS: no job row was written for the job whose scoring raised (no partial/corrupt row)")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll search-worker ranking-integration tests passed.")


if __name__ == "__main__":
    main()
