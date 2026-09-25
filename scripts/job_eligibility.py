#!/usr/bin/env python3

"""
Shared candidate-job eligibility gate.

Every job preparation/ingestion path in this project must run a job
through the SAME experience + location eligibility decision before
scoring it. This module is the single place that combines those two
existing, independent assessments into one eligibility decision --
it does not implement any parsing of its own.

Reuses, unchanged:
  - experience_eligibility.assess_experience_eligibility()
  - location_taxonomy.assess_location_eligibility()

Does NOT reuse or wrap:
  - score_job.score_job() -- scoring stays entirely separate from
    eligibility. Callers that want a score call score_job() themselves,
    on jobs this module has already deemed eligible, optionally passing
    this module's experience_assessment through to avoid score_job()
    recomputing it (the same experience_assessment=None pattern
    score_job() already supports).

candidate_profile is always an explicit argument, exactly like
score_job(), assess_experience_eligibility(), and
assess_location_eligibility() -- there is no default/global fallback
and nothing candidate-specific is hardcoded here.
"""

from dataclasses import dataclass

from experience_eligibility import Eligibility as ExperienceEligibility
from experience_eligibility import assess_experience_eligibility
from location_taxonomy import LocationEligibility, assess_location_eligibility


@dataclass
class EligibilityResult:
    eligible: bool
    experience_assessment: object
    location_assessment: object
    reason_code: str
    reason: str


def assess_job_eligibility(job, candidate_profile):
    """
    Run both eligibility gates and return one combined, deterministic
    EligibilityResult.

    Both assessments are always computed (never short-circuited) so
    that a caller excluding a job can explain the reason even when both
    experience AND location are independently disqualifying.

    Ineligible (eligible=False) only for:
      - experience_assessment.eligibility == BELOW_PROFILE
      - location_assessment.eligibility == NO_MATCH

    Eligible (eligible=True, still scorable) for every other
    combination, including:
      - experience UNKNOWN (missing/unparseable experience -- never a
        rejection on its own)
      - location UNKNOWN (missing/unparseable location -- never a
        rejection on its own)
      - experience ABOVE_PROFILE (candidate under-qualified per the
        stated minimum -- existing semantics preserved: informational,
        not disqualifying; score_job()'s Experience dimension reflects
        this via missing_skills, exactly as before this component)
      - experience MATCH/BORDERLINE and location MATCH

    This function performs no scoring and no I/O -- pure computation
    over an in-memory job dict and candidate_profile dict, safe to call
    from any preparation or ingestion path.
    """
    experience_assessment = assess_experience_eligibility(job, candidate_profile)
    location_assessment = assess_location_eligibility(job, candidate_profile)

    experience_ineligible = (
        experience_assessment.eligibility == ExperienceEligibility.BELOW_PROFILE
    )
    location_ineligible = (
        location_assessment.eligibility == LocationEligibility.NO_MATCH
    )

    eligible = not experience_ineligible and not location_ineligible

    if experience_ineligible and location_ineligible:
        reason_code = "EXPERIENCE_AND_LOCATION_INELIGIBLE"
        reason = (
            f"{experience_assessment.reason} Also: {location_assessment.reason}"
        )
    elif experience_ineligible:
        reason_code = "EXPERIENCE_BELOW_PROFILE"
        reason = experience_assessment.reason
    elif location_ineligible:
        reason_code = "LOCATION_NO_MATCH"
        reason = location_assessment.reason
    else:
        reason_code = "ELIGIBLE"
        reason = (
            "Experience and location are both compatible or "
            "inconclusive (UNKNOWN), and neither disqualifies this job."
        )

    return EligibilityResult(
        eligible=eligible,
        experience_assessment=experience_assessment,
        location_assessment=location_assessment,
        reason_code=reason_code,
        reason=reason,
    )
