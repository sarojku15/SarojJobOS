#!/usr/bin/env python3

"""
Phase 7.1 offline regression test for the reporting-semantics fixes
(data/reports/phase7_1_reporting_semantics_audit.md): the corrected,
per-candidate NEW_JOBS signal (candidate_job_matches.created_at, not
jobs.created_at), the stricter APPLY_TODAY criteria (freshness<=3
days, a usable URL, duplicate suppression), the new `Report Status`
derived label, and the explicitly-demonstrated (not silently papered
over) EMPLOYER_REJECTED limitation.

Never opens data/applications/jobos.db. Never makes a network/browser
call -- jobs come from deterministic FakeAdapter classes registered
into source_registry.ADAPTERS, per this project's established
self-contained-test-file convention.

Scenario coverage (Phase 7.1's FIFTH step, jobs A-G, verbatim):
  A. brand new, eligible, high score, fresh, not applied
  B. already seen (matched in an earlier, separate run), eligible,
     fresh, not applied
  C. already applied, eligible, fresh
  D. score-rejected (eligible, but score < 70) -- must NOT appear in
     APPLY_TODAY
  E. duplicate of Job A from another source -- must NOT appear in
     APPLY_TODAY (Job A remains the cluster representative)
  F. stale beyond 3 days (freshness_age_days > 3, independent of
     freshness CATEGORY) -- must NOT appear in APPLY_TODAY
  G. status manually set to "REJECTED" on an otherwise-qualifying job,
     demonstrating (not pretending to solve) this project's confirmed
     inability to distinguish a genuine employer rejection from the
     score-bucket's own reuse of the same status string
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
from source_adapter import JobSourceAdapter, AdapterHealth, BlockReason
from candidate_profile import normalize_candidate_profile, promote_to_confirmed, serialize_candidate_profile
from search_submission import submit_search
from search_worker import claim_next_queue_item, process_queue_item
import generate_run_report as grr


# ----------------------------------------------------------------------
# JD text tiers -- hand-verified against score_job.py's exact dimension
# keyword lists (see data/reports/phase7_reporting_audit.md, FIFTH step,
# for the same methodology).
# ----------------------------------------------------------------------

_RICH_JD = (
    "Senior Site Reliability Engineer / DevOps role. Own production reliability "
    "across AWS and Azure. Run Kubernetes (EKS/AKS) clusters, manage "
    "infrastructure as code with Terraform and Ansible, build CI/CD pipelines "
    "with Jenkins and GitHub Actions, and drive incident management, "
    "SLO/SLI/error budget practices, and root cause analysis. Observability "
    "via Prometheus and Grafana is core."
)  # scores 100 (A) -- hand-verified in Phase 7's FIFTH step

_MEDIUM_JD = (
    "Senior SRE role owning production reliability on AWS and Azure cloud "
    "infrastructure. Run Kubernetes workloads. Use Terraform for "
    "infrastructure automation. Jenkins for our build pipeline. Handle "
    "incident response and root cause analysis for our platform."
)  # scores 80 (B) -- hand-verified in Phase 7's FIFTH step

_REJECT_JD = (
    "Site Reliability Engineer role on AWS. Supporting our cloud platform "
    "infrastructure."
)  # designed to score well under 70 -- core(20) + cloud-partial(10) +
   # experience(5) + location(5) + domain(5, "cloud"+"platform") = 45,
   # no SRE/k8s/terraform/cicd/observability keywords present at all


# APPLY_TODAY requires freshness_age_days <= 3 (see generate_run_report.py).
# Anchored to the real wall-clock date rather than a fixed calendar date so
# this fixture doesn't go stale as time passes.
from datetime import date, timedelta

_TODAY = date.today().isoformat()
_FIVE_DAYS_AGO = (date.today() - timedelta(days=5)).isoformat()


def _job(job_id, source="FAKE_SOURCE_A", title="Senior Site Reliability Engineer",
         company="Example Technologies", jd_text=_RICH_JD, posted_date=None,
         experience_required="8-12 years", url_suffix=None):
    if posted_date is None:
        posted_date = _TODAY
    suffix = url_suffix or job_id.lower()
    return {
        "source": source,
        "job_id": job_id,
        "company": company,
        "title": title,
        "location": "Bengaluru",
        "work_model": "Hybrid",
        "job_url": f"https://{source.lower()}.example.com/jobs/{suffix}",
        "application_url": f"https://{source.lower()}.example.com/apply/{suffix}",
        "posted_date": posted_date,
        "jd_text": jd_text,
        "experience_required": experience_required,
        "mandatory_skills": [],
        "preferred_skills": [],
    }


JOB_A = _job("JOB-A-NEW", company="Alpha Corp", jd_text=_RICH_JD)
JOB_B = _job("JOB-B-SEEN", company="Beta Corp", jd_text=_RICH_JD)
JOB_C = _job("JOB-C-APPLIED", company="Gamma Corp", jd_text=_RICH_JD)
JOB_D = _job("JOB-D-SCOREREJ", company="Delta Corp", jd_text=_REJECT_JD)
JOB_E = _job(
    "JOB-E-DUP", source="FAKE_SOURCE_B", company="Alpha Corp",
    title="Senior Site Reliability Engineer", jd_text=_RICH_JD,
)  # same title+company+location AND same JD text as Job A (cross_source_dedup.py
   # requires description similarity >= 0.35 to flag a candidate at all -- a real,
   # existing, unmodified threshold, not a bug -- so identical text is used here
   # deliberately, both scoring 100; the representative tie-break then falls to
   # (source, job_id), deterministically picking FAKE_SOURCE_A / JOB-A-NEW
JOB_F = _job("JOB-F-STALE5D", company="Foxtrot Corp", jd_text=_RICH_JD, posted_date=_FIVE_DAYS_AGO)  # 5 days old
JOB_G = _job("JOB-G-EMPREJ", company="Golf Corp", jd_text=_RICH_JD)

ROUND_1_JOBS_A = [JOB_B]  # the "earlier run" that first discovers Job B
ROUND_2_JOBS_A = [JOB_A, JOB_B, JOB_C, JOB_D, JOB_F, JOB_G]  # "today's" run
ROUND_2_JOBS_B = [JOB_E]


# ----------------------------------------------------------------------
# Deterministic fake adapters -- no network, ever. Behavior is mutable
# per round via a module-level list this test controls directly.
# ----------------------------------------------------------------------

class _Behavior:
    jobs_a = []
    jobs_b = []


class FakeAdapterA(JobSourceAdapter):
    name = "FAKE_SOURCE_A"

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [dict(job) for job in _Behavior.jobs_a]


class FakeAdapterB(JobSourceAdapter):
    name = "FAKE_SOURCE_B"

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [dict(job) for job in _Behavior.jobs_b]


source_registry.ADAPTERS["FAKE_SOURCE_A"] = FakeAdapterA
source_registry.ADAPTERS["FAKE_SOURCE_B"] = FakeAdapterB


# ----------------------------------------------------------------------
# DB / candidate fixtures (same pattern as test_search_worker_ranking_integration.py)
# ----------------------------------------------------------------------

def _new_isolated_db():
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_run_report_71_"))
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
        "identity": {"candidate_id": candidate_id, "name": "Report Test Candidate"},
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
        (candidate_id, "Report Test Candidate", None, None, now, now, "ACTIVE"),
    )
    conn.execute(
        "INSERT INTO candidate_search_profile (candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) VALUES (?,1,'PROFILE',?,1,1,?,?)",
        (candidate_id, json.dumps(serialize_candidate_profile(profile)), now, now),
    )
    conn.commit()
    conn.close()


def _submit_and_process(db_path, candidate_id, sources):
    submit_search(str(db_path), candidate_id, sources=sources, dry_run=False)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    claimed = claim_next_queue_item(conn, candidate_id=candidate_id)
    result = process_queue_item(conn, claimed) if claimed else None
    conn.close()
    return result


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def main():
    failures = []

    print("GENERATE_RUN_REPORT PHASE 7.1 OFFLINE REGRESSION TEST (Jobs A-G, temp DB only)")
    print("=================================================================================")

    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-report", ["Senior Site Reliability Engineer"], ["Bengaluru"])

    # --- Round 1: an "earlier run" that first discovers Job B only ---
    _Behavior.jobs_a = ROUND_1_JOBS_A
    _Behavior.jobs_b = []
    result1 = _submit_and_process(db, "cand-report", ["FAKE_SOURCE_A"])
    if result1 is None or result1.status != "COMPLETED":
        _fail(failures, f"round 1 (Job B first discovery) did not complete: {result1.status if result1 else 'no result'}")
        print("\n".join(f"  {f}" for f in failures))
        sys.exit(1)
    print(f"PASS: round 1 completed -- Job B first discovered (matches_created={result1.matches_created})")

    # Backdate Job B's per-candidate "first matched" signal (and the
    # global jobs.created_at, for realism) to simulate it genuinely
    # having been seen on an EARLIER day, before today's `since` cutoff.
    backdate = "2026-09-18T00:00:00+00:00"
    conn = sqlite3.connect(db)
    conn.execute("UPDATE candidate_job_matches SET created_at = ? WHERE candidate_id = ? AND job_id = ?",
                 (backdate, "cand-report", "JOB-B-SEEN"))
    conn.execute("UPDATE jobs SET created_at = ? WHERE job_id = ?", (backdate, "JOB-B-SEEN"))
    conn.commit()
    conn.close()

    # --- Round 2: "today's" run -- rediscovers Job B, plus everything else ---
    _Behavior.jobs_a = ROUND_2_JOBS_A
    _Behavior.jobs_b = ROUND_2_JOBS_B
    result2 = _submit_and_process(db, "cand-report", ["FAKE_SOURCE_A", "FAKE_SOURCE_B"])
    if result2 is None or result2.status != "COMPLETED":
        _fail(failures, f"round 2 did not complete: {result2.status if result2 else 'no result'} / errors={result2.errors if result2 else []}")
        print("\n".join(f"  {f}" for f in failures))
        sys.exit(1)
    print(f"PASS: round 2 completed (raw={result2.raw_count}, eligible={result2.eligible_count}, scored={result2.scored_count})")

    # --- Apply the Job C (already-applied) and Job G (status manually
    # set to REJECTED, simulating a naive/incorrect attempt to record
    # an employer rejection) post-processing edits.
    #
    # THIS candidate's own candidate_job_matches.candidate_status, not
    # the global jobs.status -- the same column
    # application_lifecycle.transition_candidate_job_status() (the
    # real PATCH /api/candidates/{id}/jobs/{id}/status endpoint) writes
    # to, and the one build_report_rows() now treats as authoritative
    # over the single-candidate-era global jobs.status column (see
    # that function's own comment for why). ---
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE candidate_job_matches SET candidate_status = 'APPLIED' WHERE candidate_id = ? AND job_id = ?",
        ("cand-report", "JOB-C-APPLIED"),
    )
    conn.execute(
        "UPDATE candidate_job_matches SET candidate_status = 'REJECTED' WHERE candidate_id = ? AND job_id = ?",
        ("cand-report", "JOB-G-EMPREJ"),
    )
    conn.commit()
    conn.close()

    since_cutoff = "2026-09-19T00:00:00+00:00"  # after Job B's backdated creation, before today's round-2 timestamps

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    jobs = grr.load_jobs(conn)
    matches = grr.load_candidate_job_matches(conn, "cand-report")
    from search_submission import load_candidate_confirmed_profile
    from candidate_profile import to_legacy_matching_profile
    profile = load_candidate_confirmed_profile(conn, "cand-report")
    legacy = to_legacy_matching_profile(profile)
    conn.close()

    report_rows, duplicate_candidates = grr.build_report_rows(jobs, legacy, since=since_cutoff, candidate_job_matches=matches)
    rows_by_job_id = {row.ranking.job_id: row for row in report_rows}

    def _sheets_of(job_id):
        row = rows_by_job_id.get(job_id)
        return row.sheets if row else set()

    def _row(job_id):
        return rows_by_job_id.get(job_id)

    # === Job A: brand new, eligible, high score, fresh, not applied ===
    a = _row("JOB-A-NEW")
    if a is None:
        _fail(failures, "Job A not found")
    else:
        if a.ranking.score is None or a.ranking.score < 90:
            _fail(failures, f"Job A: expected a high score (A-tier), got {a.ranking.score}")
        if "NEW_JOBS" not in a.sheets:
            _fail(failures, f"Job A: expected NEW_JOBS, got {a.sheets}")
        if "APPLY_TODAY" not in a.sheets:
            _fail(failures, f"Job A: expected APPLY_TODAY, got {a.sheets}")
        if a.previously_seen is not False:
            _fail(failures, f"Job A: expected previously_seen=False, got {a.previously_seen}")
        if a.report_status != "READY_TO_APPLY":
            _fail(failures, f"Job A: expected report_status=READY_TO_APPLY, got {a.report_status}")
        else:
            print(f"PASS: Job A (brand new) -> NEW_JOBS + APPLY_TODAY, previously_seen=False, report_status={a.report_status}, score={a.ranking.score}")

    # === Job B: already seen (matched in round 1), eligible, fresh, not applied ===
    b = _row("JOB-B-SEEN")
    if b is None:
        _fail(failures, "Job B not found")
    else:
        if "NEW_JOBS" in b.sheets:
            _fail(failures, f"Job B: must NOT be NEW_JOBS (matched in round 1, backdated before since cutoff), got {b.sheets}")
        if "APPLY_TODAY" not in b.sheets:
            _fail(failures, f"Job B: expected still APPLY_TODAY (already-seen does not disqualify), got {b.sheets}")
        if b.previously_seen is not True:
            _fail(failures, f"Job B: expected previously_seen=True, got {b.previously_seen}")
        else:
            print(f"PASS: Job B (already seen) -> NOT in NEW_JOBS, still in APPLY_TODAY, previously_seen=True, report_status={b.report_status}")

    # === Job C: already applied, eligible, fresh ===
    c = _row("JOB-C-APPLIED")
    if c is None:
        _fail(failures, "Job C not found")
    else:
        if "ALREADY_APPLIED" not in c.sheets:
            _fail(failures, f"Job C: expected ALREADY_APPLIED, got {c.sheets}")
        if "APPLY_TODAY" in c.sheets or "ALL_MATCHING_JOBS" in c.sheets:
            _fail(failures, f"Job C: must not also be APPLY_TODAY/ALL_MATCHING_JOBS, got {c.sheets}")
        if c.report_status != "APPLIED":
            _fail(failures, f"Job C: expected report_status=APPLIED, got {c.report_status}")
        else:
            print("PASS: Job C (already applied) -> ALREADY_APPLIED only, report_status=APPLIED")

    # === Job D: score-rejected -- must NOT appear in APPLY_TODAY ===
    d = _row("JOB-D-SCOREREJ")
    if d is None:
        _fail(failures, "Job D not found")
    else:
        if not d.eligible:
            _fail(failures, f"Job D: expected eligible=True (only the score should be low), got eligible={d.eligible}")
        if d.ranking.score is None or d.ranking.score >= 70:
            _fail(failures, f"Job D: expected a score < 70, got {d.ranking.score}")
        if "APPLY_TODAY" in d.sheets:
            _fail(failures, f"Job D: must NOT appear in APPLY_TODAY, got {d.sheets}")
        if "REJECTED_EXCLUDED" not in d.sheets:
            _fail(failures, f"Job D: expected REJECTED_EXCLUDED, got {d.sheets}")
        if d.report_status != "SCORE_REJECTED_NOT_QUALIFIED":
            _fail(failures, f"Job D: expected report_status=SCORE_REJECTED_NOT_QUALIFIED, got {d.report_status}")
        else:
            print(f"PASS: Job D (score-rejected, score={d.ranking.score}) -> REJECTED_EXCLUDED, NOT APPLY_TODAY, report_status=SCORE_REJECTED_NOT_QUALIFIED")

    # === Job E: duplicate of Job A from another source -- must NOT appear in APPLY_TODAY ===
    e = _row("JOB-E-DUP")
    if e is None:
        _fail(failures, "Job E not found")
    else:
        if not duplicate_candidates:
            _fail(failures, "expected at least one duplicate_candidates entry (Job A / Job E)")
        if "DUPLICATES" not in e.sheets or "DUPLICATES" not in a.sheets:
            _fail(failures, f"expected both Job A and Job E flagged DUPLICATES: A={a.sheets} E={e.sheets}")
        if not e.duplicate_suppressed:
            _fail(failures, f"Job E: expected duplicate_suppressed=True (Job A has the higher score), got {e.duplicate_suppressed}")
        if "APPLY_TODAY" in e.sheets:
            _fail(failures, f"Job E: must NOT appear in APPLY_TODAY (suppressed duplicate), got {e.sheets}")
        if e.report_status != "DUPLICATE":
            _fail(failures, f"Job E: expected report_status=DUPLICATE, got {e.report_status}")
        if "APPLY_TODAY" not in a.sheets:
            _fail(failures, "Job A (the duplicate-cluster representative) unexpectedly excluded from APPLY_TODAY")
        else:
            print(f"PASS: Job E (duplicate of A, score={e.ranking.score}) -> suppressed, NOT APPLY_TODAY, report_status=DUPLICATE; Job A (score={a.ranking.score}) remains in APPLY_TODAY as the representative")

    # === Job F: stale beyond 3 days -- must NOT appear in APPLY_TODAY ===
    f = _row("JOB-F-STALE5D")
    if f is None:
        _fail(failures, "Job F not found")
    else:
        if f.ranking.freshness_age_days is None or f.ranking.freshness_age_days <= 3:
            _fail(failures, f"Job F: expected freshness_age_days > 3, got {f.ranking.freshness_age_days}")
        if "APPLY_TODAY" in f.sheets:
            _fail(failures, f"Job F: must NOT appear in APPLY_TODAY (beyond the 3-day apply window), got {f.sheets}")
        if "ALL_MATCHING_JOBS" not in f.sheets:
            _fail(failures, f"Job F: expected it to still qualify (ALL_MATCHING_JOBS), got {f.sheets}")
        else:
            print(f"PASS: Job F (stale, {f.ranking.freshness_age_days} days old, freshness={f.ranking.freshness}) -> excluded from APPLY_TODAY (>3 days) while still in ALL_MATCHING_JOBS")

    # === Job G: status manually set to REJECTED on an otherwise-
    # qualifying job -- demonstrates the EMPLOYER_REJECTED limitation
    # rather than pretending it is solved ===
    g = _row("JOB-G-EMPREJ")
    if g is None:
        _fail(failures, "Job G not found")
    else:
        if "ALREADY_APPLIED" in g.sheets:
            _fail(failures, f"Job G: must NOT be silently treated as ALREADY_APPLIED, got {g.sheets}")
        if "APPLY_TODAY" in g.sheets:
            _fail(failures, f"Job G: must NOT appear in APPLY_TODAY, got {g.sheets}")
        if g.report_status != "AMBIGUOUS_REJECTED_STATUS_SEE_LIMITATION":
            _fail(failures, f"Job G: expected the explicit ambiguity label, got {g.report_status} -- this system must NOT claim to know this was an employer rejection")
        else:
            print(f"PASS: Job G (status='REJECTED' on an otherwise-qualifying job) -> report_status=AMBIGUOUS_REJECTED_STATUS_SEE_LIMITATION (limitation explicitly demonstrated, not papered over)")

    # --- Workbook artifact sanity ---
    out = Path(tempfile.mkdtemp()) / "report.xlsx"
    report = grr.generate(str(db), "cand-report", out, since=since_cutoff)
    if not Path(report["workbook_path"]).exists():
        _fail(failures, "workbook not written")
    else:
        import openpyxl
        wb = openpyxl.load_workbook(report["workbook_path"])
        expected_sheets = {
            "APPLY_TODAY", "ALL_MATCHING_JOBS", "NEW_JOBS", "ALREADY_APPLIED",
            "REJECTED_EXCLUDED", "DUPLICATES", "APPLICATION_TRACKER", "SOURCE_HEALTH", "RUN_SUMMARY",
        }
        if set(wb.sheetnames) != expected_sheets:
            _fail(failures, f"workbook sheet names mismatch: got {set(wb.sheetnames)}")
        else:
            print(f"PASS: workbook written with exactly the 9 required sheets: {sorted(wb.sheetnames)}")

    if failures:
        print("\nFAILURES:")
        for fmsg in failures:
            print(f"  {fmsg}")
        sys.exit(1)

    print("\nAll Phase 7.1 reporting-semantics regression tests passed (Jobs A-G).")


if __name__ == "__main__":
    main()
