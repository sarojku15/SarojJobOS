#!/usr/bin/env python3

"""
Phase 7.2 PART E -- offline backward/forward-compatibility tests for
application status semantics (data/reports/phase7_2_automation_status_audit.md).

Confirms generate_run_report.py's `Report Status` derivation handles
every status value this codebase can produce -- past (legacy
"REJECTED"), present ("NOT_QUALIFIED"), and future (reserved
"EMPLOYER_REJECTED", not yet written by any code) -- without ever
inferring an employer rejection from an old "REJECTED" value.

Pure, offline, in-memory only -- no network, no database of any kind
(temp or production).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate_run_report as grr
from job_ranking import RankingRecord


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _qualifying_ranking(score=90, priority="A"):
    return RankingRecord(
        job_id="J1", source="TEST", title="Senior SRE", company="Acme",
        job_url="https://example.com/1", eligible=True, eligibility="ELIGIBLE",
        eligibility_reasons=[], score=score, priority=priority, status="READY_FOR_APPROVAL",
        score_explanation=None, freshness="HOT", freshness_age_days=1,
        normalized_title="senior sre", normalized_company="acme", canonical_location="Bengaluru",
    )


def _rejected_ranking(score=50):
    return RankingRecord(
        job_id="J1", source="TEST", title="Senior SRE", company="Acme",
        job_url="https://example.com/1", eligible=True, eligibility="ELIGIBLE",
        eligibility_reasons=[], score=score, priority="REJECT", status="NOT_QUALIFIED",
        score_explanation=None, freshness="HOT", freshness_age_days=1,
        normalized_title="senior sre", normalized_company="acme", canonical_location="Bengaluru",
    )


def main():
    failures = []
    print("STATUS SEMANTICS BACKWARD/FORWARD-COMPATIBILITY TEST (Phase 7.2 PART E)")
    print("=================================================================================")

    # 1. Old production-style status="REJECTED" -> SCORE_REJECTED_NOT_QUALIFIED
    ranking = _rejected_ranking()
    result = grr._compute_report_status(
        ranking, status="REJECTED", in_applied_lifecycle=False, is_apply_today_worthy=False, duplicate_suppressed=False
    )
    if result != "SCORE_REJECTED_NOT_QUALIFIED":
        _fail(failures, f"1. legacy status='REJECTED' (non-qualifying): expected SCORE_REJECTED_NOT_QUALIFIED, got {result}")
    else:
        print("PASS: 1. legacy status='REJECTED' (non-qualifying) -> SCORE_REJECTED_NOT_QUALIFIED")

    # 1b. The one edge case: legacy "REJECTED" left behind on a job that
    # was LATER re-scored to a qualifying priority (status is protected
    # from re-overwrite on re-scoring) -- must be flagged, never
    # silently treated as ready-to-apply OR as an employer rejection.
    ranking_q = _qualifying_ranking()
    result_1b = grr._compute_report_status(
        ranking_q, status="REJECTED", in_applied_lifecycle=False, is_apply_today_worthy=True, duplicate_suppressed=False
    )
    if result_1b != "AMBIGUOUS_REJECTED_STATUS_SEE_LIMITATION":
        _fail(failures, f"1b. stale legacy 'REJECTED' + now-qualifying priority: expected AMBIGUOUS_REJECTED_STATUS_SEE_LIMITATION, got {result_1b}")
    else:
        print("PASS: 1b. stale legacy 'REJECTED' + now-qualifying priority -> AMBIGUOUS_REJECTED_STATUS_SEE_LIMITATION (flagged, never silently resolved)")

    # 2. New status="NOT_QUALIFIED" -> SCORE_REJECTED_NOT_QUALIFIED
    ranking = _rejected_ranking()
    result = grr._compute_report_status(
        ranking, status="NOT_QUALIFIED", in_applied_lifecycle=False, is_apply_today_worthy=False, duplicate_suppressed=False
    )
    if result != "SCORE_REJECTED_NOT_QUALIFIED":
        _fail(failures, f"2. current status='NOT_QUALIFIED': expected SCORE_REJECTED_NOT_QUALIFIED, got {result}")
    else:
        print("PASS: 2. current status='NOT_QUALIFIED' -> SCORE_REJECTED_NOT_QUALIFIED (identical treatment to legacy 'REJECTED')")

    # 3. Explicit future status="EMPLOYER_REJECTED" -> EMPLOYER_REJECTED,
    # never conflated with a score-bucket rejection.
    ranking = _qualifying_ranking()
    result = grr._compute_report_status(
        ranking, status="EMPLOYER_REJECTED", in_applied_lifecycle=True, is_apply_today_worthy=False, duplicate_suppressed=False
    )
    if result != "EMPLOYER_REJECTED":
        _fail(failures, f"3. future status='EMPLOYER_REJECTED': expected EMPLOYER_REJECTED, got {result}")
    else:
        print("PASS: 3. status='EMPLOYER_REJECTED' -> EMPLOYER_REJECTED (unambiguous, real lifecycle status)")

    # Also confirm EMPLOYER_REJECTED correctly lands in ALREADY_APPLIED
    # sheet membership (Saroj DID apply; the employer responded).
    if "EMPLOYER_REJECTED" not in grr.APPLIED_LIFECYCLE_STATUSES:
        _fail(failures, "3b. EMPLOYER_REJECTED must be in APPLIED_LIFECYCLE_STATUSES (so it lands in ALREADY_APPLIED, not REJECTED_EXCLUDED)")
    else:
        print("PASS: 3b. EMPLOYER_REJECTED is included in APPLIED_LIFECYCLE_STATUSES")

    # And confirm the legacy/current score-bucket values are NOT
    # accidentally treated as applied-lifecycle.
    for legacy_status in ("REJECTED", "NOT_QUALIFIED"):
        if legacy_status in grr.APPLIED_LIFECYCLE_STATUSES:
            _fail(failures, f"3c. {legacy_status} must NOT be in APPLIED_LIFECYCLE_STATUSES")
    else:
        print("PASS: 3c. neither 'REJECTED' nor 'NOT_QUALIFIED' is in APPLIED_LIFECYCLE_STATUSES")

    # 4. APPLIED -> ALREADY_APPLIED (report status = literal 'APPLIED')
    ranking = _qualifying_ranking()
    result = grr._compute_report_status(
        ranking, status="APPLIED", in_applied_lifecycle=True, is_apply_today_worthy=False, duplicate_suppressed=False
    )
    if result != "APPLIED":
        _fail(failures, f"4. status='APPLIED': expected APPLIED, got {result}")
    elif "APPLIED" not in grr.APPLIED_LIFECYCLE_STATUSES:
        _fail(failures, "4. 'APPLIED' must be in APPLIED_LIFECYCLE_STATUSES")
    else:
        print("PASS: 4. status='APPLIED' -> Report Status='APPLIED', correctly in APPLIED_LIFECYCLE_STATUSES")

    # 5. GHOSTED / WITHDRAWN -> correct lifecycle classification
    for status_value in ("GHOSTED", "WITHDRAWN"):
        ranking = _qualifying_ranking()
        result = grr._compute_report_status(
            ranking, status=status_value, in_applied_lifecycle=True, is_apply_today_worthy=False, duplicate_suppressed=False
        )
        if result != status_value:
            _fail(failures, f"5. status='{status_value}': expected {status_value!r} verbatim, got {result}")
        elif status_value not in grr.APPLIED_LIFECYCLE_STATUSES:
            _fail(failures, f"5. {status_value!r} must be in APPLIED_LIFECYCLE_STATUSES")
    else:
        print("PASS: 5. status='GHOSTED'/'WITHDRAWN' -> Report Status verbatim, both correctly in APPLIED_LIFECYCLE_STATUSES")

    # Never infer employer rejection from an old "REJECTED" value --
    # explicit, direct assertion that no code path in _compute_report_status
    # ever returns "EMPLOYER_REJECTED" when the raw status is "REJECTED"
    # or "NOT_QUALIFIED".
    for legacy_status in ("REJECTED", "NOT_QUALIFIED"):
        for qualifying in (False, True):
            ranking = _qualifying_ranking() if qualifying else _rejected_ranking()
            result = grr._compute_report_status(
                ranking, status=legacy_status, in_applied_lifecycle=False,
                is_apply_today_worthy=False, duplicate_suppressed=False,
            )
            if result == "EMPLOYER_REJECTED":
                _fail(failures, f"NEVER: status={legacy_status!r} (qualifying={qualifying}) must never resolve to EMPLOYER_REJECTED, got {result}")
    else:
        print("PASS: NEVER -> 'REJECTED'/'NOT_QUALIFIED' never resolves to EMPLOYER_REJECTED under any eligibility/priority combination")

    # Production-rows-confirmed sanity: the exact 3 legacy REJECTED rows
    # observed in production (read-only query, documented in the audit
    # report) are all source='TEST', non-qualifying by construction
    # (score_job() only ever wrote REJECTED for a non-qualifying score) --
    # confirm the SCORE_BUCKET_REJECTION_STATUSES constant covers both
    # values this module must treat identically.
    if set(grr.SCORE_BUCKET_REJECTION_STATUSES) != {"NOT_QUALIFIED", "REJECTED"}:
        _fail(failures, f"SCORE_BUCKET_REJECTION_STATUSES mismatch: {grr.SCORE_BUCKET_REJECTION_STATUSES}")
    else:
        print("PASS: SCORE_BUCKET_REJECTION_STATUSES == {'NOT_QUALIFIED', 'REJECTED'} (both legacy and current score-bucket values covered)")

    if failures:
        print("\nFAILURES:")
        for fmsg in failures:
            print(f"  {fmsg}")
        sys.exit(1)

    print("\nAll Phase 7.2 status-semantics backward/forward-compatibility tests passed.")


if __name__ == "__main__":
    main()
