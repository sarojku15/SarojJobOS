#!/usr/bin/env python3

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from discover_local import normalize_job, deduplicate
from canonical_job import derive_canonical_job
from cross_source_dedup import compare_pair, find_cross_source_duplicate_candidates

FIXTURE_PATH = ROOT / "data" / "fixtures" / "canonical_jobs" / "cross_source_dataset.json"


def load_dataset():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def find_case(dataset, case_id):
    for case in dataset:
        if case["case_id"] == case_id:
            return case
    raise KeyError(f"case_id {case_id} not found in fixture")


def canonical_for(raw_job, index):
    return derive_canonical_job(normalize_job(raw_job, index))


def main():
    failures = []

    print("CROSS-SOURCE DEDUPLICATION TEST")
    print("================================")

    dataset = load_dataset()

    # ------------------------------------------------------------------
    # Pairwise cases with an explicit true/false expectation
    # ------------------------------------------------------------------
    pairwise_cases = [c for c in dataset if c["expect_cross_source_duplicate"] is not None]

    for case in pairwise_cases:
        case_id = case["case_id"]
        expected = case["expect_cross_source_duplicate"]
        raw_jobs = case["jobs"]

        if len(raw_jobs) != 2:
            failures.append(f"case {case_id}: pairwise case must have exactly 2 jobs, got {len(raw_jobs)}")
            continue

        job_a = canonical_for(raw_jobs[0], 1)
        job_b = canonical_for(raw_jobs[1], 2)

        result = compare_pair(job_a, job_b)
        is_duplicate = result is not None

        if is_duplicate != expected:
            failures.append(
                f"case {case_id} ({case['description']}): expected "
                f"cross_source_duplicate={expected}, got {is_duplicate} "
                f"(result={result})"
            )
        else:
            detail = f"confidence={result.confidence}, signals={result.signals}" if result else "correctly not a candidate"
            print(f"PASS: case {case_id} ({case['description']}) -> {detail}")

    # ------------------------------------------------------------------
    # Conservative behavior: a description mismatch overrides an
    # otherwise-matching title+company pair (case 7).
    # ------------------------------------------------------------------
    case7 = find_case(dataset, 7)
    job_a = canonical_for(case7["jobs"][0], 1)
    job_b = canonical_for(case7["jobs"][1], 2)
    if job_a.normalized_title != job_b.normalized_title or job_a.normalized_company != job_b.normalized_company:
        failures.append("case 7 precondition failed: title/company were expected to match before the description check")
    elif compare_pair(job_a, job_b) is not None:
        failures.append("case 7: SAFETY VIOLATION -- a materially different description did not override a title+company match")
    else:
        print("PASS: case 7 -> description mismatch correctly overrides an otherwise-matching title+company pair")

    # ------------------------------------------------------------------
    # Conservative behavior: a differing physical location overrides an
    # otherwise-matching title+company pair (case 6).
    # ------------------------------------------------------------------
    case6 = find_case(dataset, 6)
    job_a = canonical_for(case6["jobs"][0], 1)
    job_b = canonical_for(case6["jobs"][1], 2)
    if compare_pair(job_a, job_b) is not None:
        failures.append("case 6: SAFETY VIOLATION -- differing physical locations did not prevent a duplicate match")
    else:
        print("PASS: case 6 -> differing physical locations correctly prevent a duplicate match")

    # ------------------------------------------------------------------
    # Source provenance is never dropped: comparing two jobs never
    # mutates either CanonicalJob's own source/source_job_id/job_url.
    # ------------------------------------------------------------------
    case1 = find_case(dataset, 1)
    job_a = canonical_for(case1["jobs"][0], 1)
    job_b = canonical_for(case1["jobs"][1], 2)
    before = (job_a.source, job_a.source_job_id, job_a.job_url, job_b.source, job_b.source_job_id, job_b.job_url)
    result = compare_pair(job_a, job_b)
    after = (job_a.source, job_a.source_job_id, job_a.job_url, job_b.source, job_b.source_job_id, job_b.job_url)
    if before != after:
        failures.append("SAFETY VIOLATION: compare_pair mutated source provenance fields")
    elif result is None:
        failures.append("case 1: expected a duplicate candidate for identity-preservation check, got None")
    elif result.job_a.source == result.job_b.source:
        failures.append("case 1: both sides of the reported duplicate candidate share the same source -- provenance lost")
    else:
        print(
            f"PASS: source provenance preserved -- {result.job_a.source}:{result.job_a.source_job_id} "
            f"vs {result.job_b.source}:{result.job_b.source_job_id}"
        )

    # ------------------------------------------------------------------
    # Global run: flatten every job in the dataset, run the full
    # cross-source scan, and check exactly the expected candidate count
    # -- plus the case-16 "must never spuriously match anything" rule.
    # ------------------------------------------------------------------
    all_canonical = []
    case16_job_key = None
    index = 0
    for case in dataset:
        for raw_job in case["jobs"]:
            index += 1
            job = canonical_for(raw_job, index)
            all_canonical.append(job)
            if case["case_id"] == 16:
                case16_job_key = (job.source, job.source_job_id)

    all_candidates = find_cross_source_duplicate_candidates(all_canonical)

    expected_true_cases = [c["case_id"] for c in pairwise_cases if c["expect_cross_source_duplicate"] is True]
    if len(all_candidates) != len(expected_true_cases):
        failures.append(
            f"global scan: expected exactly {len(expected_true_cases)} duplicate candidate(s) "
            f"(cases {expected_true_cases}), got {len(all_candidates)}: "
            f"{[(c.job_a.source, c.job_a.title, c.job_b.source, c.job_b.title) for c in all_candidates]}"
        )
    else:
        print(f"PASS: global scan across all {len(all_canonical)} jobs found exactly {len(all_candidates)} duplicate candidate(s), matching cases {expected_true_cases}")

    case16_involved = any(
        case16_job_key in ((c.job_a.source, c.job_a.source_job_id), (c.job_b.source, c.job_b.source_job_id))
        for c in all_candidates
    )
    if case16_involved:
        failures.append("case 16: SAFETY VIOLATION -- the completely unrelated job spuriously matched something")
    else:
        print("PASS: case 16's completely unrelated job matched nothing in the full dataset scan")

    # ------------------------------------------------------------------
    # Same-source pairs are explicitly out of scope for this module
    # (discover_local.deduplicate() already owns that) -- confirm two
    # jobs from the SAME source are never compared here even if their
    # canonical fields would otherwise match.
    # ------------------------------------------------------------------
    same_source_a = canonical_for(dict(case1["jobs"][0], job_id="SOURCE_A-JOB-001-DUP"), 1)
    same_source_b = canonical_for(dict(case1["jobs"][0], job_id="SOURCE_A-JOB-001-DUP-2"), 2)
    same_source_candidates = find_cross_source_duplicate_candidates([same_source_a, same_source_b])
    if same_source_candidates:
        failures.append("SAFETY VIOLATION: find_cross_source_duplicate_candidates() compared two same-source jobs")
    else:
        print("PASS: two same-source jobs are never compared by find_cross_source_duplicate_candidates()")

    # ------------------------------------------------------------------
    # Compatibility with the EXISTING intra-source dedup: normalize_job()
    # + discover_local.deduplicate() must still behave exactly as before
    # for two jobs sharing (source, job_id) -- unrelated to and
    # unaffected by anything added in this task.
    # ------------------------------------------------------------------
    dup_pair = [
        normalize_job(dict(case1["jobs"][0]), 1),
        normalize_job(dict(case1["jobs"][0]), 2),
    ]
    unique_jobs, duplicate_count = deduplicate(dup_pair)
    if duplicate_count != 1 or len(unique_jobs) != 1:
        failures.append(
            f"existing intra-source deduplicate() regression: expected 1 duplicate removed, "
            f"got duplicate_count={duplicate_count}, unique_count={len(unique_jobs)}"
        )
    else:
        print("PASS: existing discover_local.deduplicate() intra-source behavior is unaffected")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll cross-source deduplication tests passed.")


if __name__ == "__main__":
    main()
