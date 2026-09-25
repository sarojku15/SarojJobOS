#!/usr/bin/env python3

"""
Read-only score-quality audit of the 20 real Naukri jobs already
captured in a temp DB from the prior live validation run.

ZERO new network requests. ZERO writes to the temp DB or to production.
This script opens the temp DB in read-only mode (file:...?mode=ro) and
never opens data/applications/jobos.db at all.

Reuses every existing production function unchanged -- no scoring,
eligibility, normalization, or freshness logic is reimplemented here:
    discover_local.normalize_job()
    job_eligibility.assess_job_eligibility()
    score_job.score_job()
    score_explanation.build_score_explanation()
    freshness.classify_freshness()
    canonical_job.derive_canonical_job()
    cross_source_dedup.find_cross_source_duplicate_candidates()
    candidate_profile.normalize_candidate_profile() / to_legacy_matching_profile()

Usage:
    python3 scripts/audit_live_naukri_scores.py [--db PATH]
"""

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from discover_local import normalize_job
from job_eligibility import assess_job_eligibility
from score_job import score_job
from score_explanation import build_score_explanation
from freshness import classify_freshness
from canonical_job import derive_canonical_job
from cross_source_dedup import find_cross_source_duplicate_candidates
from candidate_profile import normalize_candidate_profile, to_legacy_matching_profile

DEFAULT_DB = Path(
    "/var/folders/5x/hc_gpp312k1fm2bsxw0d2t2r0000gn/T/"
    "jobos_live_naukri_ranking_validation_pwgcehlj/jobos_test.db"
)

JOB_COLUMNS = [
    "job_id", "source", "company", "title", "location", "work_model",
    "job_url", "application_url", "posted_date", "discovered_at",
    "jd_text", "experience_required", "mandatory_skills", "preferred_skills",
]


def load_readonly(db_path):
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def fetch_jobs(conn):
    cols = ", ".join(JOB_COLUMNS)
    rows = conn.execute(f"SELECT {cols} FROM jobs ORDER BY id").fetchall()
    jobs = []
    for row in rows:
        record = dict(zip(JOB_COLUMNS, row))
        record["mandatory_skills"] = json.loads(record["mandatory_skills"] or "[]")
        record["preferred_skills"] = json.loads(record["preferred_skills"] or "[]")
        record["experience_min_months"] = None
        jobs.append(record)
    return jobs


def fetch_candidate_profile(conn, candidate_id="saroj"):
    row = conn.execute(
        "SELECT profile_json FROM candidate_search_profile WHERE candidate_id = ? AND is_active = 1",
        (candidate_id,),
    ).fetchone()
    raw = json.loads(row[0])
    profile = normalize_candidate_profile(raw)
    return profile, to_legacy_matching_profile(profile)


def fetch_persisted_matches(conn, candidate_id="saroj"):
    rows = conn.execute(
        "SELECT job_id, fit_score, priority, experience_eligibility, location_match, candidate_status "
        "FROM candidate_job_matches WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchall()
    return {
        r[0]: {
            "fit_score": r[1], "priority": r[2], "experience_eligibility": r[3],
            "location_match": r[4], "candidate_status": r[5],
        }
        for r in rows
    }


_ALL_DIMENSIONS = [
    "core_role", "sre_devops", "cloud", "kubernetes", "terraform",
    "cicd", "observability", "experience", "location_work_model", "overall_fit",
]


def audit_job(raw_job, index, legacy_profile, candidate_years, duplicates_by_identity, persisted_matches):
    normalized = normalize_job(dict(raw_job), index)
    canonical = derive_canonical_job(normalized)

    eligibility_result = assess_job_eligibility(normalized, legacy_profile)

    scoring = None
    explanation = None
    component_sum_matches_score = None
    explanation_matches_score_job = None
    if eligibility_result.eligible:
        scoring = score_job(normalized, legacy_profile, experience_assessment=eligibility_result.experience_assessment)
        explanation = build_score_explanation(eligibility_result, scoring)
        component_sum_matches_score = sum(explanation.components.values()) == explanation.score
        explanation_matches_score_job = (
            explanation.score == scoring["score"]
            and explanation.priority == scoring["priority"]
            and explanation.strong_matches == scoring["matched_skills"]
            and explanation.gaps == scoring["missing_skills"]
        )

    fresh = classify_freshness(normalized.get("posted_date", ""))

    key = (normalized["source"], normalized["job_id"])
    dup_entries = duplicates_by_identity.get(key, [])

    exp_min = eligibility_result.experience_assessment.parsed_min_years
    exp_max = eligibility_result.experience_assessment.parsed_max_years

    persisted = persisted_matches.get(normalized["job_id"])
    persisted_matches_recomputed = None
    if persisted is not None and scoring is not None:
        persisted_matches_recomputed = (
            persisted["fit_score"] == scoring["score"] and persisted["priority"] == scoring["priority"]
        )

    return {
        "job_id": normalized["job_id"],
        "source": normalized["source"],
        "title": normalized["title"],
        "company": normalized["company"],
        "job_url": normalized["job_url"],
        "raw_location": raw_job["location"],
        "normalized_location": normalized["location"],
        "canonical_location": canonical.canonical_location,
        "work_model": canonical.canonical_work_model,
        "experience_required_raw": normalized["experience_required"],
        "experience_min_years": exp_min,
        "experience_max_years": exp_max,
        "candidate_experience_years": candidate_years,
        "eligible": eligibility_result.eligible,
        "eligibility_reason_code": eligibility_result.reason_code,
        "eligibility_reason": eligibility_result.reason,
        "experience_eligibility": eligibility_result.experience_assessment.eligibility.value,
        "experience_eligibility_reason": eligibility_result.experience_assessment.reason,
        "location_eligibility": eligibility_result.location_assessment.eligibility.value,
        "score": scoring["score"] if scoring else None,
        "priority": scoring["priority"] if scoring else None,
        "status": scoring["status"] if scoring else None,
        "components": explanation.components if explanation else None,
        "component_sum_matches_score": component_sum_matches_score,
        "explanation_matches_score_job": explanation_matches_score_job,
        "matched_skills": scoring["matched_skills"] if scoring else [],
        "missing_skills": scoring["missing_skills"] if scoring else [],
        "mandatory_skills": normalized["mandatory_skills"],
        "preferred_skills": normalized["preferred_skills"],
        "freshness_input": normalized.get("posted_date", ""),
        "freshness_category": fresh.category.value,
        "freshness_age_days": fresh.age_days,
        "duplicate_candidates": [
            {
                "other_source": (d.job_a.source if d.job_b.source_job_id == normalized["job_id"] else d.job_b.source),
                "other_source_job_id": (d.job_a.source_job_id if d.job_b.source_job_id == normalized["job_id"] else d.job_b.source_job_id),
                "confidence": d.confidence,
            }
            for d in dup_entries
        ],
        "persisted_match": persisted,
        "persisted_matches_recomputed_score": persisted_matches_recomputed,
    }


def main():
    db_path = DEFAULT_DB
    if len(sys.argv) > 1 and sys.argv[1] == "--db":
        db_path = Path(sys.argv[2])

    if not db_path.exists():
        print(f"ERROR: temp DB not found at {db_path}")
        print("Per this audit's safety rules, no new live search will be run to regenerate it.")
        sys.exit(1)

    print(f"Auditing temp DB (read-only): {db_path}")

    conn = load_readonly(db_path)
    raw_jobs = fetch_jobs(conn)
    profile, legacy_profile = fetch_candidate_profile(conn)
    persisted_matches = fetch_persisted_matches(conn)
    conn.close()

    candidate_years = legacy_profile["candidate"]["experience_years"]

    canonical_jobs = [derive_canonical_job(normalize_job(dict(j), i)) for i, j in enumerate(raw_jobs, start=1)]
    duplicate_candidates = find_cross_source_duplicate_candidates(canonical_jobs)
    duplicates_by_identity = {}
    for d in duplicate_candidates:
        for cjob in (d.job_a, d.job_b):
            duplicates_by_identity.setdefault((cjob.source, cjob.source_job_id), []).append(d)

    audited = [
        audit_job(job, i, legacy_profile, candidate_years, duplicates_by_identity, persisted_matches)
        for i, job in enumerate(raw_jobs, start=1)
    ]

    return audited, candidate_years, profile


if __name__ == "__main__":
    audited_jobs, candidate_years, profile = main()
    out_path = ROOT / "data" / "reports" / "live_naukri_score_audit.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"candidate_experience_years": candidate_years, "jobs": audited_jobs}, indent=2))
    print(f"Wrote {out_path}")
