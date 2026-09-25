#!/usr/bin/env python3

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from offline_search_pipeline import run_offline_pipeline
from run_offline_search import load_fixture, format_text_report, to_json_report

RANKING_FIXTURE = ROOT / "data" / "fixtures" / "ranking" / "ranking_dataset.json"
EMPTY_FIXTURE = ROOT / "data" / "fixtures" / "ranking" / "empty_dataset.json"
ALL_INELIGIBLE_FIXTURE = ROOT / "data" / "fixtures" / "ranking" / "all_ineligible_dataset.json"
MALFORMED_FIXTURE = ROOT / "data" / "fixtures" / "ranking" / "malformed_dataset.json"


def main():
    failures = []

    print("OFFLINE SEARCH PIPELINE TEST")
    print("=============================")

    raw_jobs, candidate_profile = load_fixture(RANKING_FIXTURE)

    # RANK-JOB-013/014/015 assert freshness buckets (FRESH/AGING/STALE)
    # computed relative to the real wall-clock date (classify_freshness()
    # defaults to datetime.now() -- see freshness.py). Re-anchor the
    # fixture's posted_date to today() here, same as test_job_ranking.py,
    # so this test stays correct regardless of what day it actually runs.
    from datetime import date, timedelta

    _today = date.today()
    _relative_posted_days_ago = {"RANK-JOB-013": 5, "RANK-JOB-014": 10, "RANK-JOB-015": 45}
    for _job in raw_jobs:
        if _job.get("job_id") in _relative_posted_days_ago:
            _job["posted_date"] = (
                _today - timedelta(days=_relative_posted_days_ago[_job["job_id"]])
            ).isoformat()

    result = run_offline_pipeline(raw_jobs, candidate_profile)

    # --- 1. eligible job ranks above ineligible job ---
    eligible_positions = [i for i, r in enumerate(result.ranked_jobs) if r.eligible]
    ineligible_positions = [i for i, r in enumerate(result.ranked_jobs) if not r.eligible]
    if eligible_positions and ineligible_positions and max(eligible_positions) > min(ineligible_positions):
        failures.append("SAFETY VIOLATION: at least one ineligible job ranks above an eligible job")
    else:
        print("PASS: every eligible job ranks above every ineligible job")

    # --- 2. higher score ranks above lower score (within eligible jobs) ---
    eligible_scores = [r.score for r in result.ranked_jobs if r.eligible]
    if eligible_scores != sorted(eligible_scores, reverse=True):
        failures.append(f"eligible jobs are not sorted by descending score: {eligible_scores}")
    else:
        print(f"PASS: eligible jobs are sorted by descending score: {eligible_scores}")

    # --- 3. freshness breaks score ties (cases 1 vs 19: identical score, both HOT -- verify a genuine tie exists and check ordering stability instead via a synthetic tie) ---
    from offline_search_pipeline import _sort_key
    from job_ranking import RankingRecord

    tied_score_record_fresh = RankingRecord(
        job_id="Z-JOB", source="SOURCE_Z", title="X", company="Y", job_url="",
        eligible=True, eligibility="ELIGIBLE", eligibility_reasons=[],
        score=80, priority="B", status="READY_FOR_APPROVAL", score_explanation={},
        freshness="FRESH", freshness_age_days=5,
        normalized_title="x", normalized_company="y", canonical_location=None,
    )
    tied_score_record_stale = RankingRecord(
        job_id="A-JOB", source="SOURCE_A", title="X", company="Y", job_url="",
        eligible=True, eligibility="ELIGIBLE", eligibility_reasons=[],
        score=80, priority="B", status="READY_FOR_APPROVAL", score_explanation={},
        freshness="STALE", freshness_age_days=60,
        normalized_title="x", normalized_company="y", canonical_location=None,
    )
    ordered = sorted([tied_score_record_stale, tied_score_record_fresh], key=_sort_key)
    if ordered[0].freshness != "FRESH":
        failures.append("SAFETY VIOLATION: freshness did not break a genuine score tie correctly")
    else:
        print("PASS: freshness correctly breaks a score tie (FRESH ranks before STALE despite 'A-JOB' < 'Z-JOB' alphabetically)")

    # --- 4. deterministic final tie-breaker (source, job_id) when
    #     score AND freshness both tie ---
    tied_a = RankingRecord(
        job_id="JOB-B", source="SOURCE_A", title="X", company="Y", job_url="",
        eligible=True, eligibility="ELIGIBLE", eligibility_reasons=[],
        score=80, priority="B", status="READY_FOR_APPROVAL", score_explanation={},
        freshness="FRESH", freshness_age_days=5,
        normalized_title="x", normalized_company="y", canonical_location=None,
    )
    tied_b = RankingRecord(
        job_id="JOB-A", source="SOURCE_A", title="X", company="Y", job_url="",
        eligible=True, eligibility="ELIGIBLE", eligibility_reasons=[],
        score=80, priority="B", status="READY_FOR_APPROVAL", score_explanation={},
        freshness="FRESH", freshness_age_days=5,
        normalized_title="x", normalized_company="y", canonical_location=None,
    )
    ordered = sorted([tied_a, tied_b], key=_sort_key)
    if [r.job_id for r in ordered] != ["JOB-A", "JOB-B"]:
        failures.append(f"final tie-breaker is not deterministic by job_id: {[r.job_id for r in ordered]}")
    else:
        print("PASS: final tie-breaker deterministically orders by (source, job_id)")

    ordered_again = sorted([tied_a, tied_b], key=_sort_key)
    if [r.job_id for r in ordered] != [r.job_id for r in ordered_again]:
        failures.append("sort is not stable/deterministic across repeated runs")
    else:
        print("PASS: sort order is repeatable across runs")

    # --- 5. duplicate candidates remain visible ---
    if not result.duplicate_relationships:
        failures.append("expected at least one duplicate relationship in the default ranking fixture (cases 1/19)")
    else:
        rel = result.duplicate_relationships[0]
        pair = {(rel["job_a"]["source"], rel["job_a"]["source_job_id"]), (rel["job_b"]["source"], rel["job_b"]["source_job_id"])}
        if pair != {("SOURCE_A", "RANK-JOB-001"), ("SOURCE_B", "RANK-JOB-001-MIRROR")}:
            failures.append(f"unexpected duplicate relationship pair: {pair}")
        else:
            print(f"PASS: duplicate relationship surfaced -> {rel['job_a']} <-> {rel['job_b']} (confidence={rel['confidence']})")

    # --- 6. no jobs are deleted/merged: every non-malformed, non-exact-
    #     duplicate job from the fixture appears in ranked_jobs, INCLUDING
    #     both sides of a detected duplicate candidate ---
    expected_identities = {(job["source"], job.get("job_id")) for job in raw_jobs if job.get("job_id")}
    actual_identities = {(r.source, r.job_id) for r in result.ranked_jobs}
    missing = expected_identities - actual_identities
    if missing:
        failures.append(f"SAFETY VIOLATION: jobs missing from ranked output (should never be merged/deleted): {missing}")
    else:
        print(f"PASS: all {len(expected_identities)} identified source jobs are preserved in ranked_jobs (none merged/deleted)")

    both_sides_present = ("SOURCE_A", "RANK-JOB-001") in actual_identities and ("SOURCE_B", "RANK-JOB-001-MIRROR") in actual_identities
    if not both_sides_present:
        failures.append("SAFETY VIOLATION: a detected duplicate candidate pair was merged -- both sides must remain")
    else:
        print("PASS: both sides of the detected duplicate candidate remain as separate RankingRecords")

    # --- 7. score values remain unchanged (cross-check against
    #     job_ranking.build_ranking_record() called directly) ---
    from discover_local import normalize_job as _normalize_job
    from job_ranking import build_ranking_record as _build_ranking_record

    case1_raw = next(j for j in raw_jobs if j.get("job_id") == "RANK-JOB-001")
    direct_record = _build_ranking_record(_normalize_job(dict(case1_raw), 1), candidate_profile)
    pipeline_record = next(r for r in result.ranked_jobs if r.job_id == "RANK-JOB-001")
    if direct_record.score != pipeline_record.score:
        failures.append(
            f"score mismatch between direct build_ranking_record() ({direct_record.score}) and "
            f"the offline pipeline ({pipeline_record.score}) for the same job -- pipeline must not alter scoring"
        )
    else:
        print(f"PASS: score is unchanged whether computed directly or via the offline pipeline (score={direct_record.score})")

    # --- 8. score explanation remains consistent (component sum ==
    #     score, exactly as score_explanation.py already guarantees) ---
    explanation = pipeline_record.score_explanation
    if explanation is None or sum(explanation["components"].values()) != explanation["score"]:
        failures.append(f"score_explanation inconsistent after passing through the offline pipeline: {explanation}")
    else:
        print("PASS: score_explanation's component sum matches its score after passing through the offline pipeline")

    # --- 9. freshness remains independent of score (reuse cases 13/14/15
    #     from the ranking fixture: identical job content, different
    #     posted_date) ---
    fresh_ids = {"RANK-JOB-013": "FRESH", "RANK-JOB-014": "AGING", "RANK-JOB-015": "STALE"}
    scores_by_id = {r.job_id: r.score for r in result.ranked_jobs if r.job_id in fresh_ids}
    freshness_by_id = {r.job_id: r.freshness for r in result.ranked_jobs if r.job_id in fresh_ids}
    if len(set(scores_by_id.values())) != 1:
        failures.append(f"expected identical scores across cases 13/14/15 regardless of freshness, got {scores_by_id}")
    elif freshness_by_id != fresh_ids:
        failures.append(f"expected freshness categories {fresh_ids}, got {freshness_by_id}")
    else:
        print(f"PASS: freshness (FRESH/AGING/STALE) is independent of an identical match score ({list(scores_by_id.values())[0]})")

    # --- 10. zero-result dataset works ---
    empty_jobs, empty_profile = load_fixture(EMPTY_FIXTURE)
    empty_result = run_offline_pipeline(empty_jobs, empty_profile)
    if empty_result.evaluated_count != 0 or empty_result.ranked_jobs != [] or empty_result.duplicate_relationships != []:
        failures.append(f"zero-result dataset did not behave as expected: {empty_result}")
    else:
        text = format_text_report(empty_result)
        json_report = to_json_report(empty_result)
        print("PASS: zero-result dataset produces an empty, non-crashing result (text and JSON reports both render)")

    # --- 11. all-ineligible dataset works ---
    ineligible_jobs, ineligible_profile = load_fixture(ALL_INELIGIBLE_FIXTURE)
    ineligible_result = run_offline_pipeline(ineligible_jobs, ineligible_profile)
    if ineligible_result.eligible_count != 0 or ineligible_result.ineligible_count != len(ineligible_jobs):
        failures.append(f"all-ineligible dataset did not behave as expected: {ineligible_result}")
    elif any(r.eligible for r in ineligible_result.ranked_jobs):
        failures.append("all-ineligible dataset: found an eligible job where none should exist")
    else:
        format_text_report(ineligible_result)
        to_json_report(ineligible_result)
        print(f"PASS: all-ineligible dataset -> {ineligible_result.ineligible_count} ineligible, 0 eligible, reports render without error")

    # --- 12. malformed/unknown fields do not crash the pipeline ---
    malformed_jobs, malformed_profile = load_fixture(MALFORMED_FIXTURE)
    try:
        malformed_result = run_offline_pipeline(malformed_jobs, malformed_profile)
        if malformed_result.malformed_count != 2:
            failures.append(f"expected exactly 2 malformed jobs skipped, got {malformed_result.malformed_count}")
        elif malformed_result.evaluated_count != 2:
            failures.append(f"expected exactly 2 surviving jobs, got {malformed_result.evaluated_count}")
        else:
            format_text_report(malformed_result)
            to_json_report(malformed_result)
            print(
                f"PASS: malformed dataset -> {malformed_result.malformed_count} skipped, "
                f"{malformed_result.evaluated_count} processed, no crash, reports render"
            )
    except Exception as error:
        failures.append(f"SAFETY VIOLATION: run_offline_pipeline crashed on malformed input: {error!r}")

    # --- determinism of the whole pipeline end to end ---
    result_again = run_offline_pipeline(raw_jobs, candidate_profile)
    if [r.job_id for r in result.ranked_jobs] != [r.job_id for r in result_again.ranked_jobs]:
        failures.append("run_offline_pipeline is not deterministic across repeated runs on the same input")
    else:
        print("PASS: run_offline_pipeline produces identical ranking order across repeated runs")

    # --- CLI output smoke test: both text and JSON reports actually render ---
    text_report = format_text_report(result, top_n=5)
    if "OFFLINE JOB SEARCH" not in text_report or "TOP MATCHES" not in text_report:
        failures.append("format_text_report output is missing expected section headers")
    else:
        print("PASS: text report renders with expected section headers")

    json_report = to_json_report(result)
    try:
        json.dumps(json_report)
    except (TypeError, ValueError) as error:
        failures.append(f"to_json_report() output is not JSON-serializable: {error}")
    else:
        required_keys = {
            "candidate", "evaluated_count", "eligible_count", "ineligible_count",
            "ranked_jobs", "duplicate_relationships", "freshness_summary", "source_summary",
        }
        if not required_keys.issubset(json_report.keys()):
            failures.append(f"JSON report missing required keys: {required_keys - json_report.keys()}")
        else:
            print("PASS: JSON report is serializable and contains every required key")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll offline search pipeline tests passed.")


if __name__ == "__main__":
    main()
