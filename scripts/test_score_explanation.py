#!/usr/bin/env python3

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from discover_local import normalize_job
from job_eligibility import assess_job_eligibility
from score_job import score_job
from score_explanation import build_score_explanation, _ALL_DIMENSIONS

FIXTURE_PATH = ROOT / "data" / "fixtures" / "ranking" / "ranking_dataset.json"


def load_dataset():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def find_case(dataset, case_id):
    for case in dataset["cases"]:
        if case["case_id"] == case_id:
            return case
    raise KeyError(f"case_id {case_id} not found")


def evaluate(dataset, case_id):
    case = find_case(dataset, case_id)
    job = normalize_job(dict(case["job"]), case_id)
    candidate_profile = dataset["candidate_profile"]
    eligibility_result = assess_job_eligibility(job, candidate_profile)
    scoring = None
    if eligibility_result.eligible:
        scoring = score_job(job, candidate_profile, experience_assessment=eligibility_result.experience_assessment)
    return job, candidate_profile, eligibility_result, scoring


def main():
    failures = []

    print("SCORE EXPLANATION TEST")
    print("=======================")

    dataset = load_dataset()

    # --- case 1: perfect match -> full component breakdown, sum == score ---
    job, profile, eligibility_result, scoring = evaluate(dataset, 1)
    explanation = build_score_explanation(eligibility_result, scoring)

    if set(explanation.components.keys()) != set(_ALL_DIMENSIONS):
        failures.append(f"case 1: components keys mismatch, got {sorted(explanation.components.keys())}")
    elif sum(explanation.components.values()) != explanation.score:
        failures.append(
            f"case 1: component sum ({sum(explanation.components.values())}) != score ({explanation.score})"
        )
    else:
        print(f"PASS: case 1 -> score={explanation.score}, components sum matches exactly")

    if explanation.score != scoring["score"] or explanation.priority != scoring["priority"]:
        failures.append("case 1: explanation score/priority disagree with score_job()'s own output")
    else:
        print("PASS: case 1 -> explanation score/priority match score_job() exactly")

    # score preservation: the 100-point weights are untouched --
    # perfect match should score highly (score_job.py's own rubric,
    # not re-derived here).
    if explanation.score < 90:
        failures.append(f"case 1 (perfect match): expected a high score, got {explanation.score}")
    else:
        print(f"PASS: case 1 -> perfect-match score is high ({explanation.score}), matching score_job()'s rubric unchanged")

    # --- case 2: strong match with one gap (no CI/CD signal) ---
    job, profile, eligibility_result, scoring = evaluate(dataset, 2)
    explanation = build_score_explanation(eligibility_result, scoring)

    if explanation.components["cicd"] != 0:
        failures.append(f"case 2: expected cicd component == 0 (no CI/CD signal in JD), got {explanation.components['cicd']}")
    elif "CI/CD" not in explanation.gaps:
        failures.append(f"case 2: expected 'CI/CD' in gaps, got {explanation.gaps}")
    else:
        print(f"PASS: case 2 -> cicd component is 0 and 'CI/CD' correctly appears in gaps")

    if sum(explanation.components.values()) != explanation.score:
        failures.append("case 2: component sum does not match score")
    else:
        print("PASS: case 2 -> component sum matches score")

    # --- determinism: same inputs -> identical explanation ---
    job, profile, eligibility_result, scoring = evaluate(dataset, 1)
    explanation_a = build_score_explanation(eligibility_result, scoring)
    explanation_b = build_score_explanation(eligibility_result, scoring)
    if explanation_a != explanation_b:
        failures.append("build_score_explanation is not deterministic for identical inputs")
    else:
        print("PASS: build_score_explanation is deterministic")

    # --- strong_matches / gaps are exactly score_job()'s own evidence,
    #     never invented or re-derived independently ---
    if explanation_a.strong_matches != scoring["matched_skills"]:
        failures.append("strong_matches does not exactly match score_job()'s matched_skills")
    elif explanation_a.gaps != scoring["missing_skills"]:
        failures.append("gaps does not exactly match score_job()'s missing_skills")
    else:
        print("PASS: strong_matches/gaps are score_job()'s own evidence, verbatim")

    # --- case 4: below-profile experience -> ineligible, no scoring
    #     performed, and eligibility_reasons carries the real reason ---
    case4 = find_case(dataset, 4)
    job4 = normalize_job(dict(case4["job"]), 4)
    eligibility_result4 = assess_job_eligibility(job4, dataset["candidate_profile"])
    if eligibility_result4.eligible:
        failures.append("case 4: expected ineligible (BELOW_PROFILE experience), got eligible")
    else:
        print(f"PASS: case 4 -> ineligible ({eligibility_result4.reason_code}), as expected for below-profile experience")

    # --- eligibility_reasons combines eligibility-gate reason with any
    #     hard_reject_reasons from scoring, and is empty only when
    #     genuinely nothing is wrong ---
    job, profile, eligibility_result, scoring = evaluate(dataset, 1)
    explanation = build_score_explanation(eligibility_result, scoring)
    if not scoring["hard_reject_reasons"] and eligibility_result.eligible and explanation.eligibility_reasons:
        failures.append(f"case 1: expected empty eligibility_reasons for a clean eligible match, got {explanation.eligibility_reasons}")
    else:
        print("PASS: case 1 -> eligibility_reasons is empty for a clean, eligible, non-hard-rejected match")

    # --- safety net: a corrupted matched_skills label must be caught,
    #     never silently produce a wrong explanation ---
    from score_explanation import build_score_explanation as bse
    tampered_scoring = dict(scoring)
    tampered_scoring["matched_skills"] = ["This Label Does Not Exist"]
    tampered_scoring["score"] = 42  # cannot possibly match a components sum of 0
    try:
        bse(eligibility_result, tampered_scoring)
        failures.append("SAFETY VIOLATION: build_score_explanation did not raise on a component/score mismatch")
    except ValueError:
        print("PASS: build_score_explanation raises when reconstructed components disagree with the actual score")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll score-explanation tests passed.")


if __name__ == "__main__":
    main()
