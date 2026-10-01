#!/usr/bin/env python3

"""
Tests that score_job()'s 5-point Experience dimension is driven by the
single authoritative experience_eligibility assessment, and can no
longer contradict it (e.g. flagging "10-15 years" as exceeding an
11-year candidate's profile when eligibility already says MATCH).

Fully isolated: score_job()/experience_eligibility() calls are pure
in-memory computation (no DB, no network). The one test that exercises
the full prepare_jobs pipeline (test 1) redirects filter_jobs.DB_PATH to
a fresh temporary SQLite database via init_tracker.main(), so this file
never opens data/applications/jobos.db.
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import init_tracker
import filter_jobs
from score_job import score_job
from experience_eligibility import Eligibility, assess_experience_eligibility
import prepare_jobs


CANDIDATE_11_YEARS = {
    "candidate": {"experience_years": 11},
    "target_locations": ["Bengaluru"],
    "cloud": ["AWS", "Azure"],
    "kubernetes": ["Kubernetes", "EKS", "AKS"],
    "iac_and_automation": ["Terraform"],
    "cicd_and_devops": ["Jenkins", "GitHub Actions"],
    "observability": ["Prometheus", "Grafana"],
    "sre": ["SLO", "SLI", "Incident", "RCA"],
    "programming_and_scripting": ["Python"],
    "tools": [],
}

CANDIDATE_3_YEARS = {
    "candidate": {"experience_years": 3},
    "target_locations": ["Bengaluru"],
    "cloud": ["AWS", "Azure"],
    "kubernetes": ["Kubernetes", "EKS", "AKS"],
    "iac_and_automation": ["Terraform"],
    "cicd_and_devops": ["Jenkins", "GitHub Actions"],
    "observability": ["Prometheus", "Grafana"],
    "sre": ["SLO", "SLI", "Incident", "RCA"],
    "programming_and_scripting": ["Python"],
    "tools": [],
}

_JD_TEXT = (
    "SRE / DevOps role on AWS and Azure. Kubernetes (EKS/AKS), "
    "Terraform, Jenkins, GitHub Actions, Prometheus, Grafana. "
    "SLO/SLI, incident management, root cause analysis (RCA). "
    "Production, cloud platform, infrastructure, distributed systems, "
    "reliability."
)


def _job(experience_required, job_id="EXP-DIM-TEST"):
    return {
        "source": "TEST",
        "job_id": job_id,
        "company": "Example Technology",
        "title": "Senior Site Reliability Engineer",
        "location": "Bengaluru",
        "work_model": "Hybrid",
        "experience_required": experience_required,
        "jd_text": _JD_TEXT,
        "mandatory_skills": [],
        "preferred_skills": [],
    }


def _use_isolated_filter_db():
    """
    Redirect filter_jobs.DB_PATH to a fresh temporary SQLite database
    (normal production schema, initialized via init_tracker.main()) so
    the one test that runs the real prepare_jobs pipeline never opens
    data/applications/jobos.db.
    """
    tmp_dir = tempfile.mkdtemp(prefix="jobos_test_experience_dimension_")
    tmp_db_path = Path(tmp_dir) / "jobos_test.db"

    init_tracker.DATA_DIR = Path(tmp_dir)
    init_tracker.DB_PATH = tmp_db_path
    init_tracker.main()

    filter_jobs.DB_PATH = tmp_db_path


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def test_1_below_profile_excluded_by_prepare_jobs():
    """
    Candidate 11 + job 8-10 -> BELOW_PROFILE -> must never reach the
    scored `prepared` list through prepare_jobs; it must appear in
    `excluded_experience` instead, with a reason, not silently dropped.
    """
    failures = []

    _use_isolated_filter_db()

    raw_jobs = [_job("8-10 years", job_id="EXP-DIM-BELOW")]
    result = prepare_jobs._prepare_from_raw_jobs(raw_jobs, CANDIDATE_11_YEARS)

    prepared_ids = [item["job"]["job_id"] for item in result["prepared"]]
    excluded_ids = [
        item["job"]["job_id"] for item in result["excluded_experience"]
    ]

    if "EXP-DIM-BELOW" in prepared_ids:
        _fail(
            failures,
            "test 1: BELOW_PROFILE job reached the scored 'prepared' "
            f"list through prepare_jobs: {prepared_ids}",
        )
    elif "EXP-DIM-BELOW" not in excluded_ids:
        _fail(
            failures,
            "test 1: BELOW_PROFILE job was silently dropped -- not in "
            f"'excluded_experience' either: {excluded_ids}",
        )
    else:
        reason = result["excluded_experience"][0]["experience_assessment"].reason
        print(
            f"PASS: test 1 -> BELOW_PROFILE job excluded from scoring, "
            f"visible in excluded_experience with reason: {reason!r}"
        )

    return failures


def test_2_10_15_no_false_exceeds_message():
    """
    Candidate 11 + job 10-15 -> MATCH. Must NOT produce the old buggy
    "Experience requirement exceeds profile: 15 years" message.
    """
    failures = []

    job = _job("10-15 years")
    result = score_job(job, CANDIDATE_11_YEARS)

    bad_messages = [
        m for m in result["missing_skills"] if "exceeds profile" in m
    ]

    if bad_messages:
        _fail(
            failures,
            f"test 2: found the old contradictory 'exceeds profile' "
            f"message for a MATCH job: {bad_messages}",
        )
    elif "Experience" not in result["matched_skills"]:
        _fail(
            failures,
            f"test 2: expected 'Experience' in matched_skills for a "
            f"MATCH job, got matched={result['matched_skills']}",
        )
    else:
        print(
            f"PASS: test 2 -> 10-15 vs 11 scores {result['score']}, "
            f"'Experience' matched, no false exceeds-profile message"
        )

    return failures


def test_3_11_15_no_false_mismatch():
    failures = []

    job = _job("11-15 years")
    result = score_job(job, CANDIDATE_11_YEARS)

    contradictions = [
        m for m in result["missing_skills"]
        if "experience" in m.lower() or "exceeds" in m.lower()
    ]

    if contradictions:
        _fail(
            failures,
            f"test 3: found a false experience-mismatch message for a "
            f"MATCH job (exact min-edge boundary): {contradictions}",
        )
    elif "Experience" not in result["matched_skills"]:
        _fail(
            failures,
            f"test 3: expected 'Experience' in matched_skills, got "
            f"{result['matched_skills']}",
        )
    else:
        print(f"PASS: test 3 -> 11-15 vs 11 scores {result['score']}, no false mismatch")

    return failures


def test_4_11_plus_no_false_mismatch():
    failures = []

    job = _job("11+ years")
    result = score_job(job, CANDIDATE_11_YEARS)

    contradictions = [
        m for m in result["missing_skills"]
        if "experience" in m.lower() or "exceeds" in m.lower()
    ]

    if contradictions:
        _fail(
            failures,
            f"test 4: found a false experience-mismatch message for "
            f"11+ vs 11 (MATCH): {contradictions}",
        )
    elif "Experience" not in result["matched_skills"]:
        _fail(
            failures,
            f"test 4: expected 'Experience' in matched_skills, got "
            f"{result['matched_skills']}",
        )
    else:
        print(f"PASS: test 4 -> 11+ vs 11 scores {result['score']}, no false mismatch")

    return failures


def test_5_12_15_above_profile_internally_consistent():
    """
    Candidate 11 + job 12-15 -> ABOVE_PROFILE. Scoring remains allowed
    (no hard reject solely from this), but the Experience dimension must
    accurately say the candidate is below the stated minimum -- and that
    message must be exactly the eligibility layer's own reason, not a
    separately invented one.
    """
    failures = []

    job = _job("12-15 years")
    assessment = assess_experience_eligibility(job, CANDIDATE_11_YEARS)
    result = score_job(job, CANDIDATE_11_YEARS)

    if assessment.eligibility != Eligibility.ABOVE_PROFILE:
        _fail(
            failures,
            f"test 5: expected eligibility ABOVE_PROFILE, got "
            f"{assessment.eligibility}",
        )

    if result["hard_reject_reasons"]:
        _fail(
            failures,
            f"test 5: ABOVE_PROFILE alone must not force a hard reject, "
            f"got hard_reject_reasons={result['hard_reject_reasons']}",
        )

    if assessment.reason not in result["missing_skills"]:
        _fail(
            failures,
            f"test 5: expected score_job()'s missing_skills to contain "
            f"the eligibility layer's own reason verbatim "
            f"({assessment.reason!r}), got {result['missing_skills']}",
        )
    else:
        print(
            f"PASS: test 5 -> 12-15 vs 11 (ABOVE_PROFILE) scores "
            f"{result['score']}, no hard reject, consistent reason: "
            f"{assessment.reason!r}"
        )

    return failures


def test_6_unknown_no_fabricated_mismatch():
    failures = []

    job = _job("")
    result = score_job(job, CANDIDATE_11_YEARS)

    contradictions = [
        m for m in result["missing_skills"]
        if "experience" in m.lower() or "exceeds" in m.lower()
    ]

    if contradictions:
        _fail(
            failures,
            f"test 6: expected no fabricated experience mismatch for "
            f"UNKNOWN/missing experience, got {contradictions}",
        )
    elif not any("Experience" in m for m in result["matched_skills"]):
        _fail(
            failures,
            f"test 6: expected a neutral 'Experience...' entry in "
            f"matched_skills for UNKNOWN, got {result['matched_skills']}",
        )
    else:
        print(
            f"PASS: test 6 -> missing experience_required scores "
            f"{result['score']}, no fabricated mismatch, matched: "
            f"{[m for m in result['matched_skills'] if 'Experience' in m]}"
        )

    return failures


def test_7_same_job_different_candidates_score_differently():
    """
    Uses a "10-15 years" job rather than "8-10 years": an 11-year
    candidate MATCHes (keeps the 5 Experience points) while a 3-year
    candidate is ABOVE_PROFILE (loses them) -- a job where BOTH
    candidates lose the same 5 points despite being in different
    eligibility states (e.g. one BELOW_PROFILE, one ABOVE_PROFILE) would
    not actually demonstrate a difference in the resulting score, even
    though the eligibility states themselves differ.
    """
    failures = []

    job = _job("10-15 years")

    result_senior = score_job(job, CANDIDATE_11_YEARS)
    result_junior = score_job(job, CANDIDATE_3_YEARS)

    if result_senior["score"] == result_junior["score"]:
        _fail(
            failures,
            f"test 7: expected different scores for an 11-year (MATCH) "
            f"vs 3-year (ABOVE_PROFILE) candidate on the same "
            f"'10-15 years' job, both got {result_senior['score']}",
        )
    else:
        print(
            f"PASS: test 7 -> 11yr candidate (MATCH) scores "
            f"{result_senior['score']}, 3yr candidate (ABOVE_PROFILE) "
            f"scores {result_junior['score']}"
        )

    return failures


def test_8_no_hidden_global_profile_dependency():
    """
    Same guarantee as test_candidate_scoring.py's equivalent test,
    re-verified here because this component touched score_job()'s
    Experience dimension specifically -- confirms the fix did not
    reintroduce a hidden PROFILE dependency via experience_eligibility.
    """
    failures = []

    import score_job as score_job_module

    original_profile = score_job_module.PROFILE
    del score_job_module.PROFILE

    try:
        job = _job("10-15 years")
        result = score_job_module.score_job(job, CANDIDATE_11_YEARS)

        if "Experience" not in result["matched_skills"]:
            _fail(
                failures,
                f"test 8: expected scoring to succeed identically with "
                f"the module-level PROFILE global removed, got "
                f"matched={result['matched_skills']}",
            )
        else:
            print(
                "PASS: test 8 -> Experience dimension scores correctly "
                "with module-level PROFILE deleted -- no hidden "
                "global-profile dependency reintroduced"
            )
    finally:
        score_job_module.PROFILE = original_profile

    try:
        score_job(job)
    except TypeError:
        print(
            "PASS: test 8 -> score_job() still requires an explicit "
            "candidate_profile (no silent Saroj fallback)"
        )
    else:
        _fail(
            failures,
            "test 8: score_job(job) without candidate_profile unexpectedly "
            "succeeded -- a hidden default was reintroduced",
        )

    return failures


def test_9_total_score_within_bounds():
    failures = []

    cases = ["8-10 years", "10-15 years", "11-15 years", "11+ years", "12-15 years", ""]

    for exp in cases:
        job = _job(exp)
        result = score_job(job, CANDIDATE_11_YEARS)

        if not (0 <= result["score"] <= 100):
            _fail(
                failures,
                f"test 9: score out of [0, 100] bounds for "
                f"experience_required={exp!r}: {result['score']}",
            )

    if not failures:
        print("PASS: test 9 -> all scores remain within [0, 100]")

    return failures


def test_10_existing_fixtures_unchanged_except_experience_fix():
    """
    Re-run the three original scoring fixtures directly through
    score_job() (not via subprocess/CLI) and confirm priority/status are
    unchanged. data/test_job.json and data/test_job_b.json both use
    "8+ years" (MATCH under both old and new logic -- unaffected).
    data/test_job_reject.json uses "1-3 years" against an 11-year
    candidate: under the corrected logic this is now BELOW_PROFILE
    (candidate meaningfully more senior than the role wants), so its
    Experience dimension may no longer award the point it used to --
    but its priority/status are unaffected either way, because its
    title ("Junior Frontend Developer") already forces a hard reject
    independently of the Experience dimension.
    """
    import json

    failures = []

    fixtures = [
        ("data/test_job.json", "A", "READY_FOR_APPROVAL"),
        ("data/test_job_b.json", "B", "READY_FOR_APPROVAL"),
        ("data/test_job_reject.json", "REJECT", "NOT_QUALIFIED"),  # Phase 7.2: score_job.py no longer writes "REJECTED"
    ]

    # Falls back to the generic, tracked example profile when no local
    # config/profile.json exists (gitignored, operator-local -- same
    # fallback score_job.py's own module-level PROFILE uses).
    _profile_path = ROOT / "config" / "profile.json"
    if not _profile_path.exists():
        _profile_path = ROOT / "config" / "profile.json.example"
    profile = json.load(open(_profile_path, encoding="utf-8"))

    for filename, expected_priority, expected_status in fixtures:
        job = json.load(open(ROOT / filename, encoding="utf-8"))
        result = score_job(job, profile)

        if result["priority"] != expected_priority:
            _fail(
                failures,
                f"test 10: {filename} expected priority "
                f"{expected_priority}, got {result['priority']} "
                f"(score={result['score']})",
            )
        elif result["status"] != expected_status:
            _fail(
                failures,
                f"test 10: {filename} expected status {expected_status}, "
                f"got {result['status']}",
            )
        else:
            print(
                f"PASS: test 10 -> {filename} still "
                f"{result['score']}/{result['priority']}/{result['status']}"
            )

    return failures


def main():
    tests = [
        test_1_below_profile_excluded_by_prepare_jobs,
        test_2_10_15_no_false_exceeds_message,
        test_3_11_15_no_false_mismatch,
        test_4_11_plus_no_false_mismatch,
        test_5_12_15_above_profile_internally_consistent,
        test_6_unknown_no_fabricated_mismatch,
        test_7_same_job_different_candidates_score_differently,
        test_8_no_hidden_global_profile_dependency,
        test_9_total_score_within_bounds,
        test_10_existing_fixtures_unchanged_except_experience_fix,
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

    print("All experience-dimension scoring tests passed.")


if __name__ == "__main__":
    main()
