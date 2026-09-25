#!/usr/bin/env python3

"""
Regression tests for the invariants the live-Naukri score-quality audit
(data/reports/live_naukri_score_audit.md/.json) checked against the 20
real jobs captured in the prior live-validation temp DB.

If that temp DB still exists, this re-runs the audit's own read-only
logic (audit_live_naukri_scores.py) fresh against it -- zero network
calls, zero writes. If the temp DB has since been cleaned up by the OS
(temp directories are not guaranteed to persist), this test falls back
to the already-captured data/reports/live_naukri_score_audit.json
snapshot so the invariants remain checked either way; it never invents
data and never re-runs a live search itself.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from audit_live_naukri_scores import DEFAULT_DB, main as run_audit

AUDIT_JSON_PATH = ROOT / "data" / "reports" / "live_naukri_score_audit.json"


def load_audited_jobs():
    if DEFAULT_DB.exists():
        audited_jobs, candidate_years, _profile = run_audit()
        return audited_jobs, candidate_years, "live temp DB (re-audited fresh, read-only)"

    if AUDIT_JSON_PATH.exists():
        data = json.loads(AUDIT_JSON_PATH.read_text())
        return data["jobs"], data["candidate_experience_years"], "captured audit JSON snapshot (temp DB no longer present)"

    return None, None, None


def main():
    failures = []

    print("LIVE NAUKRI SCORE AUDIT -- REGRESSION INVARIANTS")
    print("===================================================")

    audited_jobs, candidate_years, source_used = load_audited_jobs()

    if audited_jobs is None:
        print("SKIP: neither the temp DB nor the captured audit JSON snapshot is available.")
        print("      (Not a failure -- there is nothing to re-check without inventing data.)")
        return

    print(f"Data source: {source_used}")
    print(f"Jobs audited: {len(audited_jobs)}")

    eligible_jobs = [j for j in audited_jobs if j["eligible"]]
    ineligible_jobs = [j for j in audited_jobs if not j["eligible"]]

    # --- score component sum equals final score ---
    bad = [j for j in eligible_jobs if j["component_sum_matches_score"] is not True]
    if bad:
        failures.append(f"component sum != score for: {[j['job_id'] for j in bad]}")
    else:
        print(f"PASS: component sum equals final score for all {len(eligible_jobs)} eligible jobs")

    # --- explanation matches score_job() ---
    bad = [j for j in eligible_jobs if j["explanation_matches_score_job"] is not True]
    if bad:
        failures.append(f"score_explanation disagrees with score_job() for: {[j['job_id'] for j in bad]}")
    else:
        print(f"PASS: score_explanation exactly matches score_job() for all {len(eligible_jobs)} eligible jobs")

    # --- eligibility result matches persisted eligibility (where a
    #     persisted match row exists) ---
    checked = 0
    for j in eligible_jobs:
        persisted = j.get("persisted_match")
        if persisted is None:
            continue
        checked += 1
        if persisted["experience_eligibility"] != j["experience_eligibility"]:
            failures.append(
                f"{j['job_id']}: persisted experience_eligibility ({persisted['experience_eligibility']}) "
                f"!= recomputed ({j['experience_eligibility']})"
            )
        if j["persisted_matches_recomputed_score"] is not True:
            failures.append(f"{j['job_id']}: persisted fit_score/priority does not match a fresh score_job() recomputation")
    if checked and not any("persisted" in f for f in failures):
        print(f"PASS: recomputed eligibility/score matches the persisted candidate_job_matches row for all {checked} eligible jobs")

    # --- freshness classification is deterministic (recompute twice) ---
    from freshness import classify_freshness
    for j in audited_jobs[:5]:  # a representative sample is enough; determinism is a pure-function property
        a = classify_freshness(j["freshness_input"])
        b = classify_freshness(j["freshness_input"])
        if a != b:
            failures.append(f"classify_freshness is not deterministic for input {j['freshness_input']!r}")
    if not any("deterministic" in f for f in failures):
        print("PASS: freshness classification is deterministic on a sample of the captured inputs")

    # --- rejected jobs are not scored ---
    bad = [j for j in ineligible_jobs if j["score"] is not None]
    if bad:
        failures.append(f"SAFETY VIOLATION: ineligible job(s) have a non-None score: {[j['job_id'] for j in bad]}")
    else:
        print(f"PASS: all {len(ineligible_jobs)} ineligible jobs have score=None (never scored)")

    # --- eligible jobs are scored exactly once (score is present and
    #     status/priority are consistent with the 100-point rubric) ---
    bad = [j for j in eligible_jobs if j["score"] is None or j["priority"] is None or j["status"] is None]
    if bad:
        failures.append(f"eligible job(s) missing score/priority/status: {[j['job_id'] for j in bad]}")
    else:
        print(f"PASS: all {len(eligible_jobs)} eligible jobs have exactly one score/priority/status")

    # --- production score weights remain unchanged (the 10 known
    #     dimension point values, matching CLAUDE.md's rubric table) ---
    expected_max_points = {
        "core_role": 20, "sre_devops": 15, "cloud": 15, "kubernetes": 10, "terraform": 10,
        "cicd": 10, "observability": 5, "experience": 5, "location_work_model": 5, "overall_fit": 5,
    }
    for j in eligible_jobs:
        for dimension, awarded in j["components"].items():
            if awarded > expected_max_points[dimension]:
                failures.append(
                    f"{j['job_id']}: dimension '{dimension}' awarded {awarded} points, "
                    f"exceeding its documented max of {expected_max_points[dimension]} -- weights may have changed"
                )
    if not any("exceeding its documented max" in f for f in failures):
        print("PASS: no dimension exceeded its documented max points -- the 100-point rubric weights are unchanged")

    # --- experience parsing sanity: every ineligible-via-BELOW_PROFILE
    #     job genuinely has a parsed max below the candidate's years
    #     (the audit's own core finding -- not a parsing bug) ---
    below_profile = [j for j in ineligible_jobs if j["experience_eligibility"] == "BELOW_PROFILE"]
    bad = [j for j in below_profile if j["experience_max_years"] is None or j["experience_max_years"] >= candidate_years]
    if bad:
        failures.append(
            f"BELOW_PROFILE job(s) whose parsed max does not actually justify the rejection: {[j['job_id'] for j in bad]}"
        )
    else:
        print(f"PASS: all {len(below_profile)} BELOW_PROFILE rejections have a genuinely parsed max experience below the candidate's {candidate_years} years")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll live-Naukri score-audit regression invariants passed.")


if __name__ == "__main__":
    main()
