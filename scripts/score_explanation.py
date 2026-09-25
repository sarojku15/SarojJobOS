#!/usr/bin/env python3

"""
Explainable-score derivation layer.

This module does NOT re-score anything and does NOT re-implement any of
score_job.py's keyword matching or dimension logic -- it only
INTERPRETS the dict score_job.score_job() already returned (score,
matched_skills, missing_skills, hard_reject_reasons) plus the
job_eligibility.EligibilityResult the caller already computed, into one
structured, per-dimension explanation.

Why matched_skills/missing_skills are reused verbatim as the evidence
lists (`strong_matches` / `gaps`) rather than re-deriving finer-grained
keyword-level evidence: score_job.py's per-dimension keyword lists
(role_keywords, sre_keywords, cloud_matches, ...) are local variables
inside score_job(), not exposed at module level. Reimplementing them
here to extract finer evidence would be a second, independent copy of
that scoring knowledge -- exactly the "competing implementation" this
project's working style forbids. Using score_job()'s own already-
computed matched/missing labels instead guarantees this explanation can
never disagree with the actual score it explains, at the cost of the
evidence being dimension-level rather than individual-keyword-level.

The one piece of numeric information score_job()'s return dict does NOT
expose -- how many of the 100 points came from each dimension -- is
reconstructed here via `_MATCHED_LABEL_COMPONENTS`, a fixed lookup table
of the exact label strings score_job() is documented to append to
matched_skills for each dimension/tier, mapped to that dimension's own
already-published point value (see CLAUDE.md's scoring table). This is
label interpretation, not scoring: build_score_explanation() asserts the
reconstructed components sum to the actual `score` and raises if they
ever disagree, so any future wording change in score_job.py's matched
labels is caught immediately as a test failure here rather than
silently producing a wrong explanation.
"""

from dataclasses import dataclass, field


_ALL_DIMENSIONS = [
    "core_role",
    "sre_devops",
    "cloud",
    "kubernetes",
    "terraform",
    "cicd",
    "observability",
    "experience",
    "location_work_model",
    "overall_fit",
]

# Exact matched_skills label strings score_job.py is documented to
# produce for each dimension/tier -> (dimension_key, points_awarded).
# Any matched_skills label NOT in this table is simply ignored for
# component reconstruction (there are none today; this is deliberately
# not a strict allowlist that raises on an unknown label, since a new,
# purely additive matched label in score_job.py should not break this
# derivation -- the sum-vs-score assertion below is what actually
# guards correctness).
_MATCHED_LABEL_COMPONENTS = {
    "Core role alignment": ("core_role", 20),
    "SRE/DevOps responsibilities": ("sre_devops", 15),
    "Partial SRE/DevOps responsibilities": ("sre_devops", 10),
    "Limited SRE/DevOps responsibilities": ("sre_devops", 5),
    "Cloud alignment": ("cloud", 15),
    "Partial cloud alignment": ("cloud", 10),
    "Kubernetes": ("kubernetes", 10),
    "Partial Kubernetes": ("kubernetes", 5),
    "Terraform/IaC": ("terraform", 10),
    "CI/CD": ("cicd", 10),
    "Partial CI/CD": ("cicd", 5),
    "Observability": ("observability", 5),
    "Experience requirement not specified": ("experience", 5),
    "Experience": ("experience", 5),
    "Location": ("location_work_model", 5),
    "Remote": ("location_work_model", 5),
    "Overall/domain fit": ("overall_fit", 5),
    "Partial domain fit": ("overall_fit", 3),
}


@dataclass
class ScoreExplanation:
    score: int
    priority: str
    status: str
    eligible: bool
    eligibility: str
    components: dict = field(default_factory=dict)
    strong_matches: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    eligibility_reasons: list = field(default_factory=list)


def _reconstruct_components(matched_skills):
    components = {dimension: 0 for dimension in _ALL_DIMENSIONS}

    for label in matched_skills:
        mapping = _MATCHED_LABEL_COMPONENTS.get(label)
        if mapping is not None:
            dimension, points = mapping
            components[dimension] = points

    return components


def build_score_explanation(eligibility_result, scoring):
    """
    Build a ScoreExplanation from an already-computed
    job_eligibility.EligibilityResult and score_job.score_job() return
    dict. Pure, deterministic, no I/O -- calling this twice with the
    same two inputs always produces an identical result.

    Raises ValueError if the reconstructed per-dimension components do
    not sum to the actual score -- see this module's docstring.
    """
    components = _reconstruct_components(scoring["matched_skills"])

    reconstructed_total = sum(components.values())
    if reconstructed_total != scoring["score"]:
        raise ValueError(
            "score_explanation: reconstructed component total "
            f"({reconstructed_total}) does not match score_job()'s "
            f"actual score ({scoring['score']}) -- score_job.py's "
            "matched_skills labels may have changed; "
            "_MATCHED_LABEL_COMPONENTS needs updating."
        )

    eligibility_reasons = []
    if not eligibility_result.eligible:
        eligibility_reasons.append(eligibility_result.reason)
    eligibility_reasons.extend(scoring["hard_reject_reasons"])

    return ScoreExplanation(
        score=scoring["score"],
        priority=scoring["priority"],
        status=scoring["status"],
        eligible=eligibility_result.eligible,
        eligibility=eligibility_result.reason_code,
        components=components,
        strong_matches=list(scoring["matched_skills"]),
        gaps=list(scoring["missing_skills"]),
        eligibility_reasons=eligibility_reasons,
    )
