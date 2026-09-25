#!/usr/bin/env python3

"""
Multi-user matching regression test (this task's Phase 8/9/17 core
property): two fixes this session made to score_job.py, verified
end-to-end against two independent candidate profiles and ONE shared
global job:

  1. evaluate_hard_reject() used to read a single global
     config/searches.json (Saroj's own exclude_keywords) applied to
     every candidate -- now reads candidate_profile["excluded_roles"].
  2. 6 of the 10 scoring dimensions (Core role, Cloud, Kubernetes, IaC,
     CI/CD, Observability -- 70 of 100 points) used to key off a single
     hardcoded, SRE/DevOps-specific keyword list regardless of which
     candidate was being scored -- now read candidate_profile's own
     target_roles/skill lists, so the SAME job genuinely scores
     differently for different candidates.

Proves:
  1. The SAME job produces materially different scores for different
     profiles (matching is genuinely per-user, never a single global
     match score on the job).
  2. Explanations (matched/missing skills) differ between profiles.
  3. Location and experience evaluation differ between profiles.
  4. excluded_roles is per-candidate -- never leaks between candidates.
  5. Neither profile's own data ever leaks into the other's result.
  6. The skill-based dimensions genuinely reflect each candidate's own
     skills, not the job's text alone.

Values below are ONLY test fixtures -- score_job.py/candidate_profile.py
have no hardcoded assumption about either profile.

Never opens data/applications/jobos.db. Never makes a network call.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from candidate_profile import normalize_candidate_profile, promote_to_confirmed, to_legacy_matching_profile
from score_job import score_job
from job_eligibility import assess_job_eligibility


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _profile(candidate_id, name, years, target_locations, cloud=None, containers=None, iac=None, cicd=None,
             observability=None, programming=None, excluded_roles=None, target_roles=None):
    raw = {
        "identity": {"candidate_id": candidate_id, "name": name},
        "professional_summary": {"total_experience_years": years},
        "skills": {
            "cloud": [{"name": s} for s in (cloud or [])],
            "containers_orchestration": [{"name": s} for s in (containers or [])],
            "infrastructure_iac": [{"name": s} for s in (iac or [])],
            "cicd": [{"name": s} for s in (cicd or [])],
            "observability": [{"name": s} for s in (observability or [])],
            "programming_scripting": [{"name": s} for s in (programming or [])],
        },
        "job_preferences": {
            "target_roles": target_roles or [],
            "excluded_roles": excluded_roles or [],
            "target_locations": target_locations,
        },
    }
    return promote_to_confirmed(normalize_candidate_profile(raw))


# Section-header example, verbatim: Senior Cloud Engineer, AWS +
# Kubernetes + Terraform, Bengaluru.
SHARED_JOB = {
    "source": "TEST", "job_id": "SHARED-JOB-1", "company": "Acme Cloud Corp",
    "title": "Senior Cloud Engineer",
    "location": "Bengaluru", "work_model": "Hybrid",
    "job_url": "https://example.com/jobs/shared-1",
    "application_url": "https://example.com/apply/shared-1",
    "posted_date": "2026-09-20",
    "jd_text": (
        "Senior Cloud Engineer role. Own AWS and Kubernetes infrastructure at scale. "
        "Manage infrastructure as code with Terraform. Build CI/CD pipelines with Jenkins. "
        "Drive incident management and root cause analysis. Observability via Prometheus and Grafana."
    ),
    "experience_required": "8-12 years",
    "mandatory_skills": [], "preferred_skills": [],
}


def main():
    failures = []
    print("MULTI-USER MATCHING REGRESSION TEST")
    print("=================================================================================")

    # USER A: matches this job strongly.
    profile_a = _profile(
        "user-a", "User A", years=11.0, target_locations=["Bengaluru"],
        cloud=["AWS"], containers=["Kubernetes"], iac=["Terraform"],
        cicd=["Jenkins"], observability=["Prometheus", "Grafana"],
        target_roles=["Senior SRE", "Senior Cloud Engineer"],
    )
    # USER B: same job, materially different profile (per the master
    # task's own worked example) -- different cloud, different stack,
    # different location, less experience.
    profile_b = _profile(
        "user-b", "User B", years=4.0, target_locations=["Hyderabad"],
        cloud=["Azure"], programming=["Python", "Spark"],
        target_roles=["Data Engineer"],
    )

    legacy_a = to_legacy_matching_profile(profile_a)
    legacy_b = to_legacy_matching_profile(profile_b)

    result_a = score_job(SHARED_JOB, legacy_a)
    result_b = score_job(SHARED_JOB, legacy_b)

    # --- 1. materially different scores for the SAME job ---
    if result_a["score"] <= result_b["score"]:
        _fail(failures, f"1. expected User A's score to be higher than User B's for the identical job, got A={result_a['score']} B={result_b['score']}")
    elif result_a["score"] - result_b["score"] < 30:
        _fail(failures, f"1. expected a MATERIAL score gap (>=30 points) now that scoring is skill-aware, got A={result_a['score']} B={result_b['score']} (gap={result_a['score'] - result_b['score']})")
    else:
        print(f"PASS: 1. the SAME shared job scores materially differently per profile: User A={result_a['score']}, User B={result_b['score']} (gap={result_a['score'] - result_b['score']})")

    # --- 2. explanations (matched/missing) differ ---
    if set(result_a["matched_skills"]) == set(result_b["matched_skills"]):
        _fail(failures, "2. expected different matched_skills between the two profiles, got identical sets")
    if set(result_a["missing_skills"]) == set(result_b["missing_skills"]):
        _fail(failures, "2. expected different missing_skills between the two profiles, got identical sets")
    if "Core role alignment" not in result_a["matched_skills"]:
        _fail(failures, f"2. expected User A to match 'Core role alignment', got {result_a['matched_skills']}")
    if not failures or not any(f.startswith("2.") for f in failures):
        print(f"PASS: 2. match explanations genuinely differ -- A matched={result_a['matched_skills']}, B matched={result_b['matched_skills']}")

    # --- 3. location evaluation differs ---
    if "Location" not in result_a["matched_skills"] and "Remote" not in result_a["matched_skills"]:
        _fail(failures, f"3. expected User A (target_locations=Bengaluru, job=Bengaluru) to match on location, got {result_a['matched_skills']}")
    if "Location" in result_b["matched_skills"] or "Remote" in result_b["matched_skills"]:
        _fail(failures, f"3. expected User B (target_locations=Hyderabad, job=Bengaluru) to NOT match on location, got {result_b['matched_skills']}")
    if not any(f.startswith("3.") for f in failures):
        print("PASS: 3. location evaluation correctly differs per profile (A matches Bengaluru, B does not)")

    # --- 4. experience evaluation differs ---
    eligibility_a = assess_job_eligibility(SHARED_JOB, legacy_a)
    eligibility_b = assess_job_eligibility(SHARED_JOB, legacy_b)
    if eligibility_a.experience_assessment.eligibility.value != "MATCH":
        _fail(failures, f"4. expected User A (11 years vs 8-12 required) to be an experience MATCH, got {eligibility_a.experience_assessment.eligibility}")
    if eligibility_b.experience_assessment.eligibility.value != "ABOVE_PROFILE":
        _fail(failures, f"4. expected User B (4 years vs 8-12 required -- the job wants MORE than User B has) to be experience ABOVE_PROFILE, got {eligibility_b.experience_assessment.eligibility}")
    if not any(f.startswith("4.") for f in failures):
        print(f"PASS: 4. experience evaluation correctly differs per profile (A={eligibility_a.experience_assessment.eligibility.value}, B={eligibility_b.experience_assessment.eligibility.value})")

    # --- 5. excluded_roles is per-candidate, never a global leak ---
    # (the exact bug this session's audit found: evaluate_hard_reject()
    # used to read a single global config/searches.json for everyone)
    frontend_job = dict(SHARED_JOB, job_id="FRONTEND-JOB-1", title="Frontend Engineer")

    profile_c = _profile("user-c", "User C", years=5.0, target_locations=["Bengaluru"], excluded_roles=["Frontend"])
    profile_d = _profile("user-d", "User D", years=5.0, target_locations=["Bengaluru"])  # no exclusions

    result_c = score_job(frontend_job, to_legacy_matching_profile(profile_c))
    result_d = score_job(frontend_job, to_legacy_matching_profile(profile_d))

    if not result_c["hard_reject_reasons"]:
        _fail(failures, f"5. expected User C (excluded_roles=['Frontend']) to hard-reject a 'Frontend Engineer' job, got no reject reasons")
    elif result_c["priority"] != "REJECT":
        _fail(failures, f"5. expected User C's hard-rejected job to have priority=REJECT, got {result_c['priority']}")
    if result_d["hard_reject_reasons"]:
        _fail(failures, f"5. SAFETY VIOLATION -- User D (no excluded_roles configured) must NOT be affected by User C's exclusion, got hard_reject_reasons={result_d['hard_reject_reasons']}")
    if not any(f.startswith("5.") for f in failures):
        print("PASS: 5. excluded_roles is genuinely per-candidate -- User C's own exclusion never leaks into User D's scoring of the identical job")

    # --- 6. VERIFIED FIX: 6 of score_job.py's 10 dimensions (Core role,
    #     Cloud, Kubernetes, IaC, CI/CD, Observability -- 70 of 100
    #     points) now read candidate_profile's OWN target_roles/skill
    #     lists instead of a single hardcoded, SRE/DevOps-specific
    #     keyword list applied to every candidate. Only "SRE/DevOps
    #     responsibilities" (15pts, generic production-practice
    #     vocabulary: SLO/SLI/incident/RCA/...) and "Overall/domain fit"
    #     (5pts, generic infra vocabulary: production/microservices/...)
    #     remain job-text-only -- deliberately, since neither maps
    #     cleanly onto a specific candidate_profile skill category
    #     without inventing new profile schema this task did not ask
    #     for. Proven directly, not assumed.
    skill_based_dimensions_a = [
        m for m in result_a["matched_skills"]
        if m not in (
            "Experience", "Experience requirement not specified", "Location", "Remote",
            "SRE/DevOps responsibilities", "Partial SRE/DevOps responsibilities", "Limited SRE/DevOps responsibilities",
            "Overall/domain fit", "Partial domain fit",
        )
    ]
    skill_based_dimensions_b = [
        m for m in result_b["matched_skills"]
        if m not in (
            "Experience", "Experience requirement not specified", "Location", "Remote",
            "SRE/DevOps responsibilities", "Partial SRE/DevOps responsibilities", "Limited SRE/DevOps responsibilities",
            "Overall/domain fit", "Partial domain fit",
        )
    ]
    if not skill_based_dimensions_a:
        _fail(failures, f"6. expected User A (AWS/Kubernetes/Terraform/target_roles=Senior SRE) to match several skill-based dimensions against this AWS/Kubernetes/Terraform job, got none (matched={result_a['matched_skills']})")
    elif skill_based_dimensions_b:
        _fail(failures, f"6. expected User B (Azure/Python/Spark/target_roles=Data Engineer) to match NONE of the AWS/Kubernetes/Terraform/SRE-titled job's skill-based dimensions, got {skill_based_dimensions_b}")
    else:
        print(f"PASS: 6. skill-based dimensions are now genuinely candidate-specific: User A matched {skill_based_dimensions_a} (real skill overlap), User B matched none (no real overlap) against the identical job")

    if not failures:
        print("\nAll multi-user matching tests passed.")
    else:
        print("\nFAILURES:")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
