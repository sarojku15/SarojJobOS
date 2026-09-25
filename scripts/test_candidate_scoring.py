#!/usr/bin/env python3

"""
Tests that score_job()/evaluate_hard_reject() are candidate-profile-aware
rather than implicitly bound to a global Saroj profile.

Fully isolated: no SQLite, no network, no dependency on
config/profile.json being loadable with any particular content -- the
candidate profiles used here are fabricated in-memory dicts, matching the
shape of config/profile.json but standing in for two materially different
candidates.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from score_job import score_job, evaluate_hard_reject


JOB = {
    "title": "Senior Site Reliability Engineer",
    "jd_text": (
        "We are looking for a Senior SRE / DevOps engineer to own "
        "production reliability across our AWS and Azure environments. "
        "You will run Kubernetes (EKS/AKS) clusters, manage "
        "infrastructure as code with Terraform, build CI/CD pipelines "
        "with Jenkins and GitHub Actions, and drive incident management, "
        "SLO/SLI/error budget practices, and root cause analysis. "
        "Observability via Prometheus and Grafana is core to this role. "
        "You will work across our cloud platform and distributed "
        "systems, supporting production infrastructure at scale."
    ),
    "location": "Bengaluru",
    "work_model": "Hybrid",
    "experience_required": "8-10 years",
    "mandatory_skills": ["Kubernetes", "Terraform"],
}


CANDIDATE_A_SRE_DEVOPS = {
    "candidate": {"experience_years": 11},
    "target_locations": ["Bengaluru", "Hyderabad", "Pune", "Chennai"],
    "target_roles": ["Senior Site Reliability Engineer", "Senior SRE", "Senior DevOps Engineer"],
    "cloud": ["AWS", "Azure"],
    "kubernetes": ["Kubernetes", "EKS", "AKS", "Helm"],
    "iac_and_automation": ["Terraform", "Ansible"],
    "cicd_and_devops": ["Jenkins", "GitHub Actions", "ArgoCD"],
    "observability": ["Prometheus", "Grafana", "Splunk", "Dynatrace"],
    "sre": ["SLO", "SLI", "SLA", "Error Budgets", "Incident Management", "RCA"],
    "programming_and_scripting": ["Python", "Shell", "PowerShell"],
    "tools": ["Linux"],
}


CANDIDATE_B_FRONTEND_MOBILE = {
    "candidate": {"experience_years": 3},
    "target_locations": ["Mumbai"],
    "target_roles": ["iOS Developer", "Mobile Engineer"],
    "cloud": [],
    "kubernetes": [],
    "iac_and_automation": [],
    "cicd_and_devops": [],
    "observability": [],
    "sre": [],
    "programming_and_scripting": ["Swift", "Kotlin", "JavaScript"],
    "tools": ["Xcode", "Android Studio"],
}


def test_two_candidates_score_same_job_differently():
    scoring_a = score_job(JOB, CANDIDATE_A_SRE_DEVOPS)
    scoring_b = score_job(JOB, CANDIDATE_B_FRONTEND_MOBILE)

    assert scoring_a["score"] != scoring_b["score"], (
        f"Expected different scores for materially different candidate "
        f"profiles on the same job, got A={scoring_a['score']} "
        f"B={scoring_b['score']}"
    )

    assert scoring_a["priority"] in ("A", "B"), (
        f"Expected candidate A (strong SRE/DevOps/AWS/Kubernetes/Terraform "
        f"fit) to score A or B priority, got {scoring_a['priority']} "
        f"(score={scoring_a['score']})"
    )

    assert scoring_b["priority"] == "REJECT", (
        f"Expected candidate B (unrelated frontend/mobile profile, "
        f"missing mandatory Kubernetes/Terraform skills) to be REJECT, "
        f"got {scoring_b['priority']} (score={scoring_b['score']})"
    )

    print(
        f"PASS: candidate A -> {scoring_a['score']} / {scoring_a['priority']}, "
        f"candidate B -> {scoring_b['score']} / {scoring_b['priority']}"
    )


def test_candidate_a_hard_reject_reasons_empty():
    reasons = evaluate_hard_reject(JOB, CANDIDATE_A_SRE_DEVOPS)

    assert reasons == [], (
        f"Expected no hard-reject reasons for candidate A "
        f"(has both mandatory skills), got {reasons}"
    )

    print("PASS: candidate A has zero hard-reject reasons")


def test_candidate_b_hard_reject_missing_mandatory_skills():
    reasons = evaluate_hard_reject(JOB, CANDIDATE_B_FRONTEND_MOBILE)

    joined = " ".join(reasons)

    assert any("Mandatory skill" in r for r in reasons), (
        f"Expected a mandatory-skill hard-reject reason for candidate B "
        f"(missing Kubernetes and Terraform), got {reasons}"
    )

    assert "Kubernetes" in joined and "Terraform" in joined, (
        f"Expected both missing mandatory skills named in the reject "
        f"reason, got {reasons}"
    )

    print(f"PASS: candidate B hard-reject reasons -> {reasons}")


def test_score_job_requires_explicit_candidate_profile():
    """
    score_job() must not silently fall back to a global/Saroj profile --
    calling it without a candidate_profile argument must fail rather than
    quietly scoring against some default.
    """
    try:
        score_job(JOB)
    except TypeError:
        print(
            "PASS: score_job() raises TypeError when called without an "
            "explicit candidate_profile (no silent Saroj fallback)"
        )
    else:
        raise AssertionError(
            "Expected score_job(job) without candidate_profile to raise "
            "TypeError, but it succeeded -- this means score_job() is "
            "silently falling back to a default/global profile."
        )


def test_scoring_does_not_depend_on_profile_json_module_global():
    """
    Deletes score_job's module-level PROFILE global entirely (simulating
    config/profile.json being absent/unloadable) and confirms score_job()
    still scores correctly using only the explicitly passed-in
    candidate_profile -- proving the scoring function itself has no
    implicit dependency on config/profile.json.
    """
    import score_job as score_job_module

    original_profile = score_job_module.PROFILE
    del score_job_module.PROFILE

    try:
        scoring = score_job_module.score_job(JOB, CANDIDATE_A_SRE_DEVOPS)

        assert scoring["priority"] in ("A", "B"), (
            f"Expected scoring to succeed identically with the module-level "
            f"PROFILE global removed, got priority={scoring['priority']} "
            f"score={scoring['score']}"
        )

        print(
            "PASS: score_job() scores correctly with module-level PROFILE "
            "global deleted -- no implicit dependency on config/profile.json"
        )
    finally:
        score_job_module.PROFILE = original_profile


def main():
    tests = [
        test_two_candidates_score_same_job_differently,
        test_candidate_a_hard_reject_reasons_empty,
        test_candidate_b_hard_reject_missing_mandatory_skills,
        test_score_job_requires_explicit_candidate_profile,
        test_scoring_does_not_depend_on_profile_json_module_global,
    ]

    failures = []

    for test in tests:
        try:
            test()
        except AssertionError as error:
            failures.append(f"{test.__name__}: {error}")
            print(f"FAIL: {test.__name__}: {error}")

    print()

    if failures:
        print(f"{len(failures)} test(s) failed.")
        sys.exit(1)

    print("All candidate-profile scoring tests passed.")


if __name__ == "__main__":
    main()
