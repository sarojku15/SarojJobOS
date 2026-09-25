#!/usr/bin/env python3

import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from discover_local import normalize_job
from canonical_job import derive_canonical_job
from cross_source_dedup import DuplicateCandidate
from job_ranking import build_ranking_record
from freshness import FreshnessCategory

FIXTURE_PATH = ROOT / "data" / "fixtures" / "ranking" / "ranking_dataset.json"


def load_dataset():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def find_case(dataset, case_id):
    for case in dataset["cases"]:
        if case["case_id"] == case_id:
            return case
    raise KeyError(f"case_id {case_id} not found")


def build_for_case(dataset, case_id):
    case = find_case(dataset, case_id)
    job = normalize_job(dict(case["job"]), case_id)
    return build_ranking_record(job, dataset["candidate_profile"])


def main():
    failures = []

    print("JOB RANKING RECORD TEST")
    print("=========================")

    dataset = load_dataset()

    # Cases 13/14/15/18 assert freshness buckets (FRESH/AGING/STALE/future)
    # computed relative to the real wall-clock date (classify_freshness()
    # defaults to datetime.now() -- see freshness.py). The fixture's
    # posted_date values are therefore re-anchored to today() here rather
    # than stored as absolute dates, so this test stays correct regardless
    # of what day it actually runs.
    from datetime import date, timedelta

    _today = date.today()
    _relative_posted_days_ago = {13: 5, 14: 10, 15: 45, 18: -5}
    for _case_id, _days_ago in _relative_posted_days_ago.items():
        find_case(dataset, _case_id)["job"]["posted_date"] = (
            _today - timedelta(days=_days_ago)
        ).isoformat()

    # --- case 1: perfect match -> eligible, scored, all fields populated ---
    record = build_for_case(dataset, 1)
    if not record.eligible or record.score is None or record.score_explanation is None:
        failures.append(f"case 1: expected eligible=True with a populated score/explanation, got {record}")
    elif record.normalized_title != "senior site reliability engineer":
        failures.append(f"case 1: unexpected normalized_title {record.normalized_title!r}")
    elif record.canonical_location != "Bengaluru":
        failures.append(f"case 1: unexpected canonical_location {record.canonical_location!r}")
    else:
        print(f"PASS: case 1 -> eligible, score={record.score}, priority={record.priority}, normalized_title={record.normalized_title!r}")

    # --- case 4: below-profile experience -> ineligible, score/priority/
    #     status/score_explanation must all be None (never scored) ---
    record = build_for_case(dataset, 4)
    if record.eligible:
        failures.append("case 4: expected ineligible for below-profile experience")
    elif record.score is not None or record.priority is not None or record.status is not None or record.score_explanation is not None:
        failures.append(
            f"case 4: expected score/priority/status/score_explanation all None for an ineligible job, got "
            f"score={record.score}, priority={record.priority}, status={record.status}, "
            f"score_explanation={record.score_explanation}"
        )
    elif not record.eligibility_reasons:
        failures.append("case 4: expected a non-empty eligibility_reasons for an ineligible job")
    else:
        print(f"PASS: case 4 -> ineligible, no score computed, eligibility_reasons={record.eligibility_reasons}")

    # --- interaction between eligibility and ranking: ineligibility
    #     for LOCATION (case 6) behaves the same way as experience ---
    record = build_for_case(dataset, 6)
    if record.eligible or record.score is not None:
        failures.append(f"case 6 (wrong city): expected ineligible with score=None, got eligible={record.eligible}, score={record.score}")
    else:
        print("PASS: case 6 (wrong city) -> ineligible, no score computed")

    # --- case 5: above-profile experience -> still eligible and scored,
    #     informational only ---
    record = build_for_case(dataset, 5)
    if not record.eligible or record.score is None:
        failures.append(f"case 5 (above-profile experience): expected eligible and scored, got {record}")
    else:
        print(f"PASS: case 5 (above-profile experience) -> eligible and scored (score={record.score})")

    # --- case 7: remote job matches the candidate's generic Remote
    #     preference -> eligible ---
    record = build_for_case(dataset, 7)
    if not record.eligible:
        failures.append(f"case 7 (remote job): expected eligible, got {record.eligibility}: {record.eligibility_reasons}")
    else:
        print("PASS: case 7 (remote job) -> eligible via candidate's generic Remote preference")

    # --- case 8: unknown location -> eligible (UNKNOWN is never a
    #     rejection on its own) ---
    record = build_for_case(dataset, 8)
    if not record.eligible:
        failures.append(f"case 8 (unknown location): expected eligible, got {record.eligibility}")
    else:
        print("PASS: case 8 (unknown location) -> still eligible, informational only")

    # --- cases 9/10/11: AWS/Azure/Kubernetes-heavy jobs score their
    #     respective components fully ---
    record9 = build_for_case(dataset, 9)
    if record9.score_explanation["components"]["cloud"] != 15:
        failures.append(f"case 9 (AWS-heavy): expected cloud component 15, got {record9.score_explanation['components']}")
    else:
        print("PASS: case 9 (AWS-heavy) -> cloud component fully scored")

    record10 = build_for_case(dataset, 10)
    if record10.score_explanation["components"]["cloud"] != 15:
        failures.append(f"case 10 (Azure-heavy): expected cloud component 15, got {record10.score_explanation['components']}")
    else:
        print("PASS: case 10 (Azure-heavy) -> cloud component fully scored")

    record11 = build_for_case(dataset, 11)
    if record11.score_explanation["components"]["kubernetes"] != 10:
        failures.append(f"case 11 (Kubernetes-heavy): expected kubernetes component 10, got {record11.score_explanation['components']}")
    else:
        print("PASS: case 11 (Kubernetes-heavy) -> kubernetes component fully scored")

    # --- case 12: irrelevant job -> low score, still eligible (location
    #     matches, no hard-reject keyword) ---
    record = build_for_case(dataset, 12)
    if record.score is None or record.score >= 70:
        failures.append(f"case 12 (irrelevant job): expected a low score (<70), got {record.score}")
    else:
        print(f"PASS: case 12 (irrelevant job) -> low score ({record.score}), priority={record.priority}")

    # --- freshness wiring: freshness never influences score, and vice
    #     versa (cases 13/14/15: same job content, different posted_date) ---
    record13 = build_for_case(dataset, 13)
    record14 = build_for_case(dataset, 14)
    record15 = build_for_case(dataset, 15)
    if record13.freshness != FreshnessCategory.FRESH.value:
        failures.append(f"case 13: expected FRESH, got {record13.freshness}")
    elif record14.freshness != FreshnessCategory.AGING.value:
        failures.append(f"case 14: expected AGING, got {record14.freshness}")
    elif record15.freshness != FreshnessCategory.STALE.value:
        failures.append(f"case 15: expected STALE, got {record15.freshness}")
    elif not (record13.score == record14.score == record15.score):
        failures.append(
            f"SAFETY VIOLATION: freshness affected the match score -- "
            f"{record13.score} / {record14.score} / {record15.score} should be identical"
        )
    else:
        print(
            f"PASS: cases 13/14/15 -> freshness = FRESH/AGING/STALE respectively, "
            f"identical match score ({record13.score}) across all three -- freshness never alters scoring"
        )

    # --- cases 16/17/18: missing / malformed / future posted_date -> UNKNOWN freshness ---
    for case_id, label in [(16, "missing"), (17, "malformed"), (18, "future")]:
        record = build_for_case(dataset, case_id)
        if record.freshness != FreshnessCategory.UNKNOWN.value:
            failures.append(f"case {case_id} ({label} posted_date): expected UNKNOWN freshness, got {record.freshness}")
        else:
            print(f"PASS: case {case_id} ({label} posted_date) -> UNKNOWN freshness")

    # --- ranking record construction: all required fields present and
    #     of the right shape ---
    record = build_for_case(dataset, 1)
    required_fields = [
        "job_id", "source", "title", "company", "job_url", "eligible", "eligibility",
        "eligibility_reasons", "score", "priority", "status", "score_explanation",
        "freshness", "freshness_age_days", "requirement_type", "normalized_title",
        "normalized_company", "canonical_location", "duplicate_candidates",
    ]
    missing_fields = [f for f in required_fields if not hasattr(record, f)]
    if missing_fields:
        failures.append(f"RankingRecord missing expected fields: {missing_fields}")
    else:
        print("PASS: RankingRecord exposes every required field")

    # --- duplicate-candidate information, when supplied, is correctly
    #     summarized against the OTHER side of the pair ---
    case1 = find_case(dataset, 1)
    job_a_raw = dict(case1["job"])
    job_b_raw = dict(case1["job"])
    job_b_raw["source"] = "SOURCE_B"
    job_b_raw["job_id"] = "RANK-JOB-001-MIRROR"

    normalized_a = normalize_job(job_a_raw, 1)
    normalized_b = normalize_job(job_b_raw, 2)
    canonical_a = derive_canonical_job(normalized_a)
    canonical_b = derive_canonical_job(normalized_b)

    fake_candidate = DuplicateCandidate(
        job_a=canonical_a, job_b=canonical_b, confidence="HIGH",
        signals=["normalized_title_match", "normalized_company_match"], reason="test fixture",
    )

    record_with_dup = build_ranking_record(normalized_a, dataset["candidate_profile"], duplicate_candidates=[fake_candidate])
    if len(record_with_dup.duplicate_candidates) != 1:
        failures.append(f"expected exactly 1 duplicate_candidates entry, got {record_with_dup.duplicate_candidates}")
    elif record_with_dup.duplicate_candidates[0]["other_source"] != "SOURCE_B":
        failures.append(f"expected other_source == 'SOURCE_B', got {record_with_dup.duplicate_candidates[0]}")
    else:
        print(f"PASS: duplicate-candidate information correctly summarized -> {record_with_dup.duplicate_candidates[0]}")

    # --- no duplicate_candidates supplied -> empty list, never None/crash ---
    record_no_dup = build_for_case(dataset, 1)
    if record_no_dup.duplicate_candidates != []:
        failures.append(f"expected duplicate_candidates == [] when none supplied, got {record_no_dup.duplicate_candidates}")
    else:
        print("PASS: duplicate_candidates defaults to [] when not supplied")

    # --- compatibility: score_job() is NEVER called for an ineligible
    #     job (eligibility-before-scoring, mirroring search_worker.py) ---
    with mock.patch("job_ranking.score_job") as mocked_score_job:
        build_for_case(dataset, 4)  # below-profile experience -> ineligible
        if mocked_score_job.called:
            failures.append("SAFETY VIOLATION: score_job() was called for an ineligible job")
        else:
            print("PASS: score_job() is never called for an ineligible job (eligibility-before-scoring preserved)")

    with mock.patch("job_ranking.score_job", wraps=__import__("score_job").score_job) as mocked_score_job:
        build_for_case(dataset, 1)  # eligible
        if not mocked_score_job.called:
            failures.append("expected score_job() to be called for an eligible job")
        else:
            print("PASS: score_job() IS called for an eligible job")

    # --- requirement_type: threaded onto RankingRecord, REQUIRED/
    #     PREFERRED/UNKNOWN all preserved, and never changes the
    #     eligibility decision itself (same experience range, only the
    #     qualifier word differs) ---
    base_case = find_case(dataset, 1)
    variants = [
        ("8-12 years preferred", "PREFERRED"),
        ("8-12 years required", "REQUIRED"),
        ("8-12 years mandatory", "REQUIRED"),
        ("8-12 years", "UNKNOWN"),
    ]
    eligibility_seen = set()
    for experience_text, expected_type in variants:
        variant_job = dict(base_case["job"])
        variant_job["experience_required"] = experience_text
        job = normalize_job(variant_job, f"req-type-{expected_type}")
        record = build_ranking_record(job, dataset["candidate_profile"])
        eligibility_seen.add(record.eligibility)
        if record.requirement_type != expected_type:
            failures.append(
                f"requirement_type: {experience_text!r} expected {expected_type}, got {record.requirement_type}"
            )
    if len(eligibility_seen) > 1:
        failures.append(
            f"SAFETY VIOLATION: requirement_type wording changed the eligibility decision -- saw {eligibility_seen}"
        )
    if not any(f.startswith("requirement_type:") or f.startswith("SAFETY VIOLATION: requirement_type") for f in failures):
        print(
            f"PASS: requirement_type correctly threaded onto RankingRecord for all of "
            f"{[t for _, t in variants]}, eligibility identical across wording ({eligibility_seen})"
        )

    # --- determinism: identical input -> identical RankingRecord ---
    record_a = build_for_case(dataset, 1)
    record_b = build_for_case(dataset, 1)
    if record_a != record_b:
        failures.append("build_ranking_record is not deterministic for identical inputs")
    else:
        print("PASS: build_ranking_record is deterministic")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll job-ranking tests passed.")


if __name__ == "__main__":
    main()
