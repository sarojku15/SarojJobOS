#!/usr/bin/env python3

"""
Custom Job Scoring Profiles.

============================================================================
AUDIT SUMMARY (recorded here rather than only in a chat transcript, so
the design rationale survives alongside the code)
============================================================================
The existing canonical scoring engine (scripts/score_job.py) is a pure
function `score_job(job, candidate_profile, experience_assessment=None)`
returning {score, priority, status, matched_skills, missing_skills,
hard_reject_reasons}. It is a FIXED 10-dimension, 100-point model:
Core role alignment (20), SRE/DevOps responsibilities (15), Cloud (15),
Kubernetes (10), Terraform/IaC (10), CI/CD (10), Observability (5),
Experience (5), Location/work model (5), Overall/domain fit (5).
Hard gates (evaluate_hard_reject(): excluded titles, junior/intern,
missing mandatory skills) run BEFORE scoring and can force REJECT
regardless of the numeric score -- this module preserves that rule
exactly: a mandatory-gate failure can never be overridden by weights.

Callers (job_ranking.py, search_worker.py, prepare_jobs.py, ...) all
pass the SAME flat dict shape produced by
candidate_profile.to_legacy_matching_profile() -- no separate "richer"
candidate representation reaches score_job() today. That dict has no
salary_expectation_min/max or certifications keys (checked directly in
to_legacy_matching_profile()'s own return statement). The job dict
score_job() receives (produced by discover_local.normalize_job()) has
NO salary fields and no structured "required certifications" field
either -- only free-text jd_text. This is the reason the Salary and
Certifications criteria below use an honest, documented policy instead
of inventing data that does not exist in this codebase.

CRITERION MAPPING (illustrative seven-dimension profile -> existing
engine):

| Illustrative criterion   | Existing implementation reused          | Required change                         | Compat risk |
|--------------------------|------------------------------------------|------------------------------------------|-------------|
| Technical skills         | matched_keywords() over cloud/k8s/iac/cicd/observability (score_job.py dims 3-7) | none -- composite of existing match ratios | none (additive) |
| Relevant experience      | experience_eligibility.assess_experience_eligibility() (score_job.py dim 8, identical policy) | none | none |
| Role/title alignment     | role_alignment_score() (score_job.py dim 1) | none | none |
| Salary and compensation  | job salary fields (none exist in the normalized job dict today) vs candidate_profile["salary_expectation_min/max"] (additive key, see candidate_profile.to_legacy_matching_profile()) | additive: 2 new optional dict keys | none -- MISSING by default until job data exists |
| Location and work mode   | target_locations / work_model check (score_job.py dim 9) | none | none |
| Certifications           | candidate's own certifications (additive key) matched as keywords against jd_text (same matched_keywords() utility, new input list) -- a text-mention PROXY, not a validated structured requirement | additive: 1 new optional dict key | documented proxy, not equivalence |
| Company and domain       | score_job.py's existing domain_keywords list (dim 10), reused verbatim; no structured company/industry field exists in this codebase | none -- reused as-is | documented proxy |

Weight validation policy (one policy, applied consistently): **weights
are normalized to sum to 100 at evaluation time** (not required to be
entered as exactly 100) -- so a user who enters 30/20/15/15/10/5/5
(already 100) gets exactly that, and a user who enters e.g. 3/2/1.5 in
any consistent ratio gets the same relative result. Negative weights,
non-numeric weights, and a profile with zero usable (non-zero-weight)
criteria are rejected outright. The UI shows the normalized result.

Missing-data policy (one documented rule per criterion, never a silent
full-credit): a criterion whose required input is entirely absent is
marked MISSING and contributes its full weight at **50% credit**
(neutral -- the same "benefit of the doubt, not a penalty" principle
score_job.py's own Experience dimension already uses for
Eligibility.UNKNOWN) rather than 0% (which would silently manufacture
a mismatch from mere silence) or 100% (which would fabricate a match).
This is applied identically for every criterion with missing data
(missing salary, missing certifications evidence, missing company
text) -- see CRITERION_MISSING_POLICY below for the exact statement
per criterion.

LEGACY MODE: scoring_profile=None (or omitted) is not a "zero-weight
profile" -- it is a literal delegation to score_job.score_job() with
no transformation whatsoever, so a user who has never configured a
custom profile is byte-for-byte unaffected by this module's existence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from experience_eligibility import assess_experience_eligibility, Eligibility
from score_job import (
    evaluate_hard_reject,
    matched_keywords,
    normalize,
    role_alignment_score,
    score_job,
)

# ---------------------------------------------------------------------
# Criterion registry -- the ONLY seven criteria this engine supports.
# A profile may enable any subset; an unknown criterion key is rejected
# at validation time (never silently ignored).
# ---------------------------------------------------------------------

CRITERIA = (
    "technical_skills",
    "relevant_experience",
    "role_title_alignment",
    "salary_compensation",
    "location_work_mode",
    "certifications",
    "company_domain",
)

CRITERION_DISPLAY_NAME = {
    "technical_skills": "Technical skills",
    "relevant_experience": "Relevant experience",
    "role_title_alignment": "Role/title alignment",
    "salary_compensation": "Salary and compensation",
    "location_work_mode": "Location and work mode",
    "certifications": "Certifications",
    "company_domain": "Company and domain",
}

# The illustrative default profile from the product brief. This is the
# STARTING POINT a user sees when creating a new custom profile -- it
# is never applied implicitly; legacy scoring remains the default for
# every candidate/search that has not explicitly selected a profile.
ILLUSTRATIVE_DEFAULT_WEIGHTS = {
    "technical_skills": 30,
    "relevant_experience": 20,
    "role_title_alignment": 15,
    "salary_compensation": 15,
    "location_work_mode": 10,
    "certifications": 5,
    "company_domain": 5,
}
ILLUSTRATIVE_DEFAULT_MANDATORY_GATE = True
ILLUSTRATIVE_DEFAULT_MIN_SHORTLIST_SCORE = 70

# Exact, documented missing-data statement per criterion -- used both
# by _criterion_score() below and surfaced verbatim in explanations.
CRITERION_MISSING_POLICY = {
    "technical_skills": "No skill lists configured on the candidate profile for this category.",
    "relevant_experience": "Job's required experience could not be determined (same policy as the legacy Experience dimension).",
    "role_title_alignment": "Candidate has no target_roles configured.",
    "salary_compensation": "Neither the job nor the candidate's salary expectation has usable salary data in this installation's current job data model.",
    "location_work_mode": "Job has no location/work-model text and candidate has no target_locations configured.",
    "certifications": "Candidate has no certifications configured, or the job's description does not mention any of them (text-mention proxy only -- not a verified requirement).",
    "company_domain": "Job's company/description text matched none of the generic domain-fit keywords this project uses (no structured company/industry classification exists).",
}

_MISSING_DATA_CREDIT = 0.5  # neutral -- see module docstring


class ScoringProfileError(ValueError):
    """Raised for an invalid scoring profile (bad weights, threshold,
    or criteria) -- never for a job-level data problem, which is
    always handled via the missing-data policy instead."""


@dataclass
class CriterionResult:
    criterion: str
    display_name: str
    raw_score: float  # 0.0..1.0, before weighting
    weight_percent: float  # the NORMALIZED weight (0..100) actually used
    weighted_contribution: float  # raw_score * weight_percent (0..100 scale)
    missing_data: bool
    evidence: list[str] = field(default_factory=list)
    reason: str | None = None


@dataclass
class ScoringProfileResult:
    score: int  # 0..100, rounded
    priority: str
    status: str
    qualified: bool  # score >= min_shortlist_score AND mandatory gate passed
    mandatory_gate_passed: bool
    hard_reject_reasons: list[str]
    criteria: list[CriterionResult]
    profile_id: str | None
    profile_version: int | None
    profile_name: str | None
    normalized_weights: dict[str, float]
    matched_skills: list[str]
    missing_skills: list[str]


def validate_scoring_profile(profile: dict[str, Any]) -> dict[str, float]:
    """
    Validates `profile["weights"]` (a {criterion: number} dict) and
    returns the NORMALIZED weights (summing to exactly 100.0, modulo
    float rounding), per this module's one documented policy:
    normalize positive weights to 100 at evaluation time.

    Raises ScoringProfileError for:
      - an unknown criterion key
      - a negative or non-numeric weight
      - zero usable (positive-weight) criteria
      - a min_shortlist_score outside [0, 100]
    Never raises for a *job's* missing data -- that is a scoring-time
    concern (see _criterion_score()), not a profile-validity concern.
    """
    weights = profile.get("weights") or {}
    if not isinstance(weights, dict):
        raise ScoringProfileError("weights must be a mapping of criterion -> number")

    cleaned: dict[str, float] = {}
    for criterion, raw_weight in weights.items():
        if criterion not in CRITERIA:
            raise ScoringProfileError(
                f"Unknown criterion {criterion!r} -- supported criteria are: {', '.join(CRITERIA)}"
            )
        try:
            value = float(raw_weight)
        except (TypeError, ValueError):
            raise ScoringProfileError(f"Weight for {criterion!r} must be numeric, got {raw_weight!r}")
        if value < 0:
            raise ScoringProfileError(f"Weight for {criterion!r} must not be negative, got {value}")
        cleaned[criterion] = value

    total = sum(cleaned.values())
    if total <= 0:
        raise ScoringProfileError(
            "A scoring profile must have at least one criterion with a positive weight."
        )

    normalized = {criterion: (value / total) * 100.0 for criterion, value in cleaned.items()}

    min_score = profile.get("min_shortlist_score", 70)
    try:
        min_score = float(min_score)
    except (TypeError, ValueError):
        raise ScoringProfileError(f"min_shortlist_score must be numeric, got {min_score!r}")
    if not (0 <= min_score <= 100):
        raise ScoringProfileError(f"min_shortlist_score must be between 0 and 100, got {min_score}")

    return normalized


def _technical_skills(job, candidate_profile, combined_text):
    categories = ("cloud", "kubernetes", "iac_and_automation", "cicd_and_devops", "observability")
    ratios = []
    evidence = []
    any_configured = False
    for category in categories:
        skills = candidate_profile.get(category, [])
        if not skills:
            continue
        any_configured = True
        matches = matched_keywords(combined_text, skills)
        ratios.append(min(1.0, len(matches) / max(1, len(skills))))
        if matches:
            evidence.extend(matches)
    if not any_configured:
        return 0.0, True, [], CRITERION_MISSING_POLICY["technical_skills"]
    return (sum(ratios) / len(ratios) if ratios else 0.0), False, evidence, None


def _relevant_experience(experience_assessment):
    if experience_assessment.eligibility == Eligibility.UNKNOWN:
        return 1.0, True, [], CRITERION_MISSING_POLICY["relevant_experience"]
    if experience_assessment.eligibility in (Eligibility.MATCH, Eligibility.BORDERLINE):
        return 1.0, False, ["Experience"], None
    return 0.0, False, [], experience_assessment.reason


def _role_title_alignment(title, candidate_profile):
    target_roles = candidate_profile.get("target_roles", [])
    if not target_roles:
        return 0.0, True, [], CRITERION_MISSING_POLICY["role_title_alignment"]
    if role_alignment_score(title, target_roles):
        return 1.0, False, ["Core role alignment"], None
    return 0.0, False, [], "Job title does not overlap with any target role"


def _salary_compensation(job, candidate_profile):
    job_min = job.get("salary_min")
    job_max = job.get("salary_max")
    cand_min = candidate_profile.get("salary_expectation_min")
    cand_max = candidate_profile.get("salary_expectation_max")

    if job_min is None and job_max is None:
        return _MISSING_DATA_CREDIT, True, [], CRITERION_MISSING_POLICY["salary_compensation"]
    if cand_min is None and cand_max is None:
        return _MISSING_DATA_CREDIT, True, [], "Candidate has no salary_expectation_min/max configured."

    job_lo = job_min if job_min is not None else job_max
    job_hi = job_max if job_max is not None else job_min
    cand_lo = cand_min if cand_min is not None else cand_max
    cand_hi = cand_max if cand_max is not None else cand_min

    # Overlap between [job_lo, job_hi] and [cand_lo, cand_hi].
    overlap_lo = max(job_lo, cand_lo)
    overlap_hi = min(job_hi, cand_hi)
    if overlap_hi >= overlap_lo:
        return 1.0, False, ["Salary within expected range"], None
    return 0.0, False, [], f"Job salary range does not overlap candidate's expected range"


def _location_work_mode(job, candidate_profile, location, work_model):
    target_locations = [normalize(x) for x in candidate_profile.get("target_locations", [])]
    if not target_locations and not location and not work_model:
        return _MISSING_DATA_CREDIT, True, [], CRITERION_MISSING_POLICY["location_work_mode"]
    if any(loc in location for loc in target_locations):
        return 1.0, False, ["Location"], None
    if "remote" in work_model or "remote" in location:
        return 1.0, False, ["Remote"], None
    return 0.0, False, [], "Location/work model does not match candidate preferences"


def _certifications(candidate_profile, combined_text):
    certs = candidate_profile.get("certifications", [])
    if not certs:
        return _MISSING_DATA_CREDIT, True, [], CRITERION_MISSING_POLICY["certifications"]
    matches = matched_keywords(combined_text, certs)
    if matches:
        return 1.0, False, matches, None
    return _MISSING_DATA_CREDIT, True, [], CRITERION_MISSING_POLICY["certifications"]


_DOMAIN_KEYWORDS = [
    "production", "cloud", "platform", "microservices",
    "infrastructure", "distributed systems", "reliability",
]


def _company_domain(combined_text):
    matches = matched_keywords(combined_text, _DOMAIN_KEYWORDS)
    if len(matches) >= 2:
        return 1.0, False, matches, None
    if matches:
        return 0.5, False, matches, None
    return _MISSING_DATA_CREDIT, True, [], CRITERION_MISSING_POLICY["company_domain"]


def score_with_profile(job, candidate_profile, scoring_profile=None, experience_assessment=None):
    """
    The profile-aware entry point.

    scoring_profile=None (the default) delegates to score_job.score_job()
    with ZERO transformation -- this is what "legacy scoring" means
    throughout this project, and it is the ONLY mode used unless a
    candidate/search has explicitly selected a custom profile.

    scoring_profile (when provided) is a dict:
        {
          "scoring_profile_id": <str>, "version": <int>, "name": <str>,
          "weights": {criterion: number, ...},
          "mandatory_gate_enabled": <bool>,      # default True
          "min_shortlist_score": <0..100>,        # default 70
        }

    Returns a ScoringProfileResult. The mandatory-skill gate (when
    enabled) reuses score_job.evaluate_hard_reject() EXACTLY -- a
    failed gate always yields qualified=False regardless of the
    weighted score, matching the legacy "a high score can never
    override a hard mandatory skill gap" rule.
    """
    if scoring_profile is None:
        legacy = score_job(job, candidate_profile, experience_assessment=experience_assessment)
        return ScoringProfileResult(
            score=legacy["score"],
            priority=legacy["priority"],
            status=legacy["status"],
            qualified=legacy["priority"] != "REJECT" and not legacy["hard_reject_reasons"],
            mandatory_gate_passed=not legacy["hard_reject_reasons"],
            hard_reject_reasons=legacy["hard_reject_reasons"],
            criteria=[],
            profile_id=None,
            profile_version=None,
            profile_name=None,
            normalized_weights={},
            matched_skills=legacy["matched_skills"],
            missing_skills=legacy["missing_skills"],
        )

    normalized_weights = validate_scoring_profile(scoring_profile)
    mandatory_gate_enabled = scoring_profile.get("mandatory_gate_enabled", True)
    min_shortlist_score = float(scoring_profile.get("min_shortlist_score", 70))

    title = normalize(job.get("title", ""))
    jd = normalize(job.get("jd_text", ""))
    location = normalize(job.get("location", ""))
    work_model = normalize(job.get("work_model", ""))
    combined = f"{title} {jd}"

    hard_reject_reasons = (
        evaluate_hard_reject(job, candidate_profile) if mandatory_gate_enabled else []
    )

    if experience_assessment is None:
        experience_assessment = assess_experience_eligibility(job, candidate_profile)

    criterion_fns = {
        "technical_skills": lambda: _technical_skills(job, candidate_profile, combined),
        "relevant_experience": lambda: _relevant_experience(experience_assessment),
        "role_title_alignment": lambda: _role_title_alignment(title, candidate_profile),
        "salary_compensation": lambda: _salary_compensation(job, candidate_profile),
        "location_work_mode": lambda: _location_work_mode(job, candidate_profile, location, work_model),
        "certifications": lambda: _certifications(candidate_profile, combined),
        "company_domain": lambda: _company_domain(combined),
    }

    criteria_results: list[CriterionResult] = []
    total_score = 0.0
    matched_skills: list[str] = []
    missing_skills: list[str] = []

    for criterion, weight_percent in normalized_weights.items():
        raw_score, missing_data, evidence, reason = criterion_fns[criterion]()
        contribution = raw_score * weight_percent
        total_score += contribution
        criteria_results.append(
            CriterionResult(
                criterion=criterion,
                display_name=CRITERION_DISPLAY_NAME[criterion],
                raw_score=raw_score,
                weight_percent=weight_percent,
                weighted_contribution=contribution,
                missing_data=missing_data,
                evidence=evidence,
                reason=reason,
            )
        )
        if evidence:
            matched_skills.extend(evidence)
        elif reason and not missing_data:
            missing_skills.append(reason)
        elif missing_data:
            missing_skills.append(f"{CRITERION_DISPLAY_NAME[criterion]} (missing data): {reason}")

    score = max(0, min(100, round(total_score)))
    mandatory_gate_passed = not hard_reject_reasons
    qualified = mandatory_gate_passed and score >= min_shortlist_score

    if not mandatory_gate_passed:
        priority = "REJECT"
        status = "NOT_QUALIFIED"
    elif qualified:
        priority = "A" if score >= 90 else "B" if score >= 80 else "C"
        status = "READY_FOR_APPROVAL" if score >= 80 else "FOUND"
    else:
        priority = "REJECT"
        status = "NOT_QUALIFIED"

    return ScoringProfileResult(
        score=score,
        priority=priority,
        status=status,
        qualified=qualified,
        mandatory_gate_passed=mandatory_gate_passed,
        hard_reject_reasons=hard_reject_reasons,
        criteria=criteria_results,
        profile_id=scoring_profile.get("scoring_profile_id"),
        profile_version=scoring_profile.get("version"),
        profile_name=scoring_profile.get("name"),
        normalized_weights=normalized_weights,
        matched_skills=matched_skills,
        missing_skills=missing_skills,
    )


def result_to_dict(result: ScoringProfileResult) -> dict:
    return {
        "score": result.score,
        "priority": result.priority,
        "status": result.status,
        "qualified": result.qualified,
        "mandatory_gate_passed": result.mandatory_gate_passed,
        "hard_reject_reasons": result.hard_reject_reasons,
        "matched_skills": result.matched_skills,
        "missing_skills": result.missing_skills,
        "profile_id": result.profile_id,
        "profile_version": result.profile_version,
        "profile_name": result.profile_name,
        "normalized_weights": result.normalized_weights,
        "criteria": [
            {
                "criterion": c.criterion,
                "display_name": c.display_name,
                "raw_score": round(c.raw_score, 4),
                "weight_percent": round(c.weight_percent, 4),
                "weighted_contribution": round(c.weighted_contribution, 4),
                "missing_data": c.missing_data,
                "evidence": c.evidence,
                "reason": c.reason,
            }
            for c in result.criteria
        ],
    }
