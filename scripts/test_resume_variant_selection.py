#!/usr/bin/env python3

"""
Regression test for item 10's real architecture bug: resume_variant
used to live ONLY on the global `jobs` table, so two different
candidates matched to the SAME job could never have two different
selected resumes/variants recorded -- whichever candidate was
processed last would silently overwrite the other's value (exactly the
same class of bug candidate_status already had and was already fixed
for, see search_worker.py's _upsert_job_scored() docstring).

Covers:
  1. A candidate with no resume on file -> resume_id/resume_variant
     stay honestly None, never guessed.
  2. Two candidates each upload a DIFFERENT resume, are matched to the
     SAME global job -> each candidate's own candidate_job_matches row
     carries ITS OWN resume_id/resume_variant, independent of the
     other's.
  3. The read path (api/results_store.py, generate_run_report.py)
     prefers the candidate-scoped value over a stale legacy value on
     the global jobs.resume_variant column.
  4. Re-uploading byte-identical content does not create a duplicate
     `resumes` row (UNIQUE(candidate_id, content_hash)).
  5. A later resume upload + re-match (re-run) refreshes resume_variant
     on the match row -- a system-computed fact, not a protected human
     decision.
  6. No fabricated resume content or variant labels anywhere: the
     label is always either an explicit resume_version or the
     candidate's own real uploaded filename.

Fully offline (no PDF parsing, no HTTP layer) -- exercises
resume_store.py / resume_variant_selector.py / search_worker.
upsert_candidate_job_match() / results_store.py / generate_run_report.py
directly against an isolated temp DB. Never touches jobos.db.
"""

import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
API_DIR = ROOT / "api"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(API_DIR))

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


production_before = _sha(PRODUCTION_DB)

import init_dev_db
import tracker as tracker_module
from candidate_profile import normalize_candidate_profile, promote_to_confirmed, serialize_candidate_profile
from discover_local import normalize_job
from score_job import score_job
from job_eligibility import assess_job_eligibility
from search_worker import upsert_candidate_job_match
import resume_store
from resume_variant_selector import select_resume_for_candidate
import generate_run_report as report_mod
import results_store

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


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _make_confirmed_candidate(conn, candidate_id, name, target_role):
    now = _now()
    conn.execute(
        "INSERT INTO candidates (candidate_id, name, email, phone, created_at, updated_at, status) "
        "VALUES (?,?,?,?,?,?,?)",
        (candidate_id, name, None, None, now, now, "ACTIVE"),
    )
    raw_profile = {
        "identity": {"candidate_id": candidate_id, "name": name},
        "professional_summary": {"total_experience_years": 10},
        "skills": {
            "cloud": [{"name": "AWS"}, {"name": "Azure"}],
            "containers_orchestration": [{"name": "Kubernetes"}],
            "infrastructure_iac": [{"name": "Terraform"}],
            "cicd": [{"name": "Jenkins"}],
            "observability": [{"name": "Prometheus"}],
        },
        "job_preferences": {"target_roles": [target_role], "target_locations": ["Bangalore"]},
    }
    confirmed = promote_to_confirmed(normalize_candidate_profile(raw_profile))
    conn.execute(
        "INSERT INTO candidate_search_profile (candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) "
        "VALUES (?,1,'PROFILE',?,1,1,?,?)",
        (candidate_id, json.dumps(serialize_candidate_profile(confirmed)), now, now),
    )
    conn.commit()
    return confirmed


tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_resume_variant_"))
tmp_db = tmp_dir / "jobos_test.db"
init_dev_db.init_dev_db(tmp_db)

conn = sqlite3.connect(tmp_db)
conn.execute("PRAGMA foreign_keys = ON")

candidate_a = "cand_variant_a"
candidate_b = "cand_variant_b"
profile_a = _make_confirmed_candidate(conn, candidate_a, "Candidate A", "Senior SRE")
profile_b = _make_confirmed_candidate(conn, candidate_b, "Candidate B", "Senior SRE")

job = normalize_job(
    {
        "source": "FAKE_VARIANT_SOURCE",
        "job_source": "FAKE_VARIANT_SOURCE",
        "company": "SharedJobCo",
        "title": "Senior SRE",
        "jd_text": "kubernetes terraform aws azure jenkins prometheus slo sla incident rca production cloud platform",
        "job_url": "https://example.com/jobs/shared-variant-job-1",
        "location": "Bangalore",
        "experience_required": "8-12 years",
    },
    1,
)

from candidate_profile import to_legacy_matching_profile

scoring_a = score_job(job, to_legacy_matching_profile(profile_a))
tracker_module.upsert_job(conn, job, scoring_a)
conn.commit()
elig_a = assess_job_eligibility(job, to_legacy_matching_profile(profile_a))
elig_b = assess_job_eligibility(job, to_legacy_matching_profile(profile_b))
scoring_b = score_job(job, to_legacy_matching_profile(profile_b))

# --- 1. no resume on file yet -> honestly None, never guessed ---
resume_id_none, variant_none = select_resume_for_candidate(conn, candidate_a)
check(resume_id_none is None and variant_none is None, f"1. candidate with no resume on file gets (None, None), got ({resume_id_none!r}, {variant_none!r})")

upsert_candidate_job_match(conn, candidate_a, job, scoring_a, elig_a, None)
row = conn.execute(
    "SELECT resume_id, resume_variant FROM candidate_job_matches WHERE candidate_id = ? AND job_id = ?",
    (candidate_a, job["job_id"]),
).fetchone()
check(row == (None, None), f"1. match row for a candidate with no resume has resume_id/resume_variant = (None, None), got {row}")

# --- 2. two candidates upload DIFFERENT resumes, matched to the SAME
#     job -> each gets ITS OWN resume_id/resume_variant, independently ---
resume_id_a = resume_store.record_uploaded_resume(
    conn, candidate_a, "CandidateA_SRE_Resume.pdf", b"fake pdf bytes for candidate A", "/fake/a.pdf", status="PARSED"
)
resume_id_b = resume_store.record_uploaded_resume(
    conn, candidate_b, "CandidateB_DevOps_Resume.pdf", b"completely different bytes for B", "/fake/b.pdf", status="PARSED"
)
check(resume_id_a != resume_id_b, "2. two different candidates' uploads get different resume_id values")

upsert_candidate_job_match(conn, candidate_a, job, scoring_a, elig_a, None)  # re-match: refreshes resume fields
upsert_candidate_job_match(conn, candidate_b, job, scoring_b, elig_b, None)

row_a = conn.execute(
    "SELECT resume_id, resume_variant FROM candidate_job_matches WHERE candidate_id = ? AND job_id = ?",
    (candidate_a, job["job_id"]),
).fetchone()
row_b = conn.execute(
    "SELECT resume_id, resume_variant FROM candidate_job_matches WHERE candidate_id = ? AND job_id = ?",
    (candidate_b, job["job_id"]),
).fetchone()

check(row_a[0] == resume_id_a, f"2. candidate A's match row carries A's own resume_id, got {row_a[0]}")
check(row_b[0] == resume_id_b, f"2. candidate B's match row carries B's own resume_id, got {row_b[0]}")
check(row_a[0] != row_b[0], "2. SAFETY: the SAME global job has DIFFERENT resume_id for the two candidates -- no cross-candidate leak")
check(row_a[1] == "CandidateA_SRE_Resume", f"2. candidate A's resume_variant label is grounded in A's own real filename (no resume_version set), got {row_a[1]!r}")
check(row_b[1] == "CandidateB_DevOps_Resume", f"2. candidate B's resume_variant label is grounded in B's own real filename, got {row_b[1]!r}")
check(row_a[1] != row_b[1], "2. SAFETY: the SAME global job has DIFFERENT resume_variant for the two candidates")

# --- 3. read path prefers candidate-scoped value over a stale legacy
#     global jobs.resume_variant value ---
conn.execute("UPDATE jobs SET resume_variant = ? WHERE job_id = ?", ("LEGACY_STALE_VALUE", job["job_id"]))
conn.commit()

# api/db.py's real get_conn() always sets row_factory = sqlite3.Row on
# every connection handed to results_store.py -- matching that here
# since this test uses a raw sqlite3.connect() instead of the real
# connection factory.
conn.row_factory = sqlite3.Row
persisted = results_store._load_persisted_results(conn, candidate_a, {job["job_id"]})
check(len(persisted) == 1, "3. setup: persisted-path result found for candidate A")
check(persisted[0]["resume_variant"] == "CandidateA_SRE_Resume", f"3. api/results_store.py's persisted-read path prefers the candidate-scoped resume_variant over the stale legacy global value, got {persisted[0]['resume_variant']!r}")

jobs_loaded = report_mod.load_jobs(conn, exclude_test_mock=False)
matches_a = report_mod.load_candidate_job_matches(conn, candidate_a)
rows_a, _ = report_mod.build_report_rows(jobs_loaded, to_legacy_matching_profile(profile_a), candidate_job_matches=matches_a)
report_row_a = next(r for r in rows_a if r.ranking.job_id == job["job_id"])
check(report_row_a.resume_variant == "CandidateA_SRE_Resume", f"3. generate_run_report.py's live-rescore path also prefers the candidate-scoped resume_variant over the stale legacy global value, got {report_row_a.resume_variant!r}")

# --- 4. re-uploading byte-identical content does not duplicate the row ---
before_count = conn.execute("SELECT COUNT(*) FROM resumes WHERE candidate_id = ?", (candidate_a,)).fetchone()[0]
resume_id_a_again = resume_store.record_uploaded_resume(
    conn, candidate_a, "CandidateA_SRE_Resume.pdf", b"fake pdf bytes for candidate A", "/fake/a.pdf", status="PARSED"
)
after_count = conn.execute("SELECT COUNT(*) FROM resumes WHERE candidate_id = ?", (candidate_a,)).fetchone()[0]
check(resume_id_a_again == resume_id_a, "4. re-uploading byte-identical content returns the SAME resume_id")
check(before_count == after_count, f"4. re-uploading byte-identical content does not create a duplicate resumes row (before={before_count}, after={after_count})")

# --- 5. a genuinely NEW resume upload + re-match refreshes resume_variant
#     (system-computed fact, unlike candidate_status which is protected) ---
resume_id_a_v2 = resume_store.record_uploaded_resume(
    conn, candidate_a, "CandidateA_SRE_Resume_v2.pdf", b"genuinely different bytes -- a real new version", "/fake/a_v2.pdf", status="PARSED"
)
check(resume_id_a_v2 != resume_id_a, "5. uploading genuinely different content creates a NEW resumes row/resume_id")

upsert_candidate_job_match(conn, candidate_a, job, scoring_a, elig_a, None)
row_a_after_v2 = conn.execute(
    "SELECT resume_id, resume_variant FROM candidate_job_matches WHERE candidate_id = ? AND job_id = ?",
    (candidate_a, job["job_id"]),
).fetchone()
check(row_a_after_v2[0] == resume_id_a_v2, f"5. a re-match after a new resume upload refreshes resume_id to the newest resume, got {row_a_after_v2[0]}")
check(row_a_after_v2[1] == "CandidateA_SRE_Resume_v2", f"5. resume_variant label refreshes to the newest resume's filename, got {row_a_after_v2[1]!r}")

# --- 6. no fabrication anywhere: candidate B was never given any of
#     Saroj-specific config/profile.json's SRE-A/DEVOPS-A/... labels --
#     every label observed above came from an actual uploaded filename ---
all_variants_seen = {row_a[1], row_b[1], row_a_after_v2[1]}
saroj_specific_labels = {"SRE-A", "DEVOPS-A", "AZURE-A", "AWS-A", "PLATFORM-A"}
check(not (all_variants_seen & saroj_specific_labels), f"6. no Saroj-specific config-derived variant label leaked into a real selection, saw {all_variants_seen}")

conn.close()

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
