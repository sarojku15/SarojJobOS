#!/usr/bin/env python3

"""
Candidate search profile: "what this candidate wants searched," kept
deliberately separate from candidate_profile.CandidateProfile ("what
this candidate is") and from a search RUN ("what the candidate asked
the system to search at this particular time" -- see
search_submission.py).

Built from a CONFIRMED CandidateProfile plus a handful of
search-specific parameters (sources, minimum match score, result cap,
optional experience bounds) that do not belong on the candidate's own
intrinsic profile. Nothing here is specific to any one candidate: every field comes
either from the CandidateProfile passed in, or from a generic,
candidate-agnostic default.

Reuses, unchanged -- no second parser/taxonomy of any kind:
  - candidate_profile.py (CandidateProfile, ProfileStatus)
  - location_taxonomy.py (only to classify whether a target location is
    remote-type, for the informational has_remote_preference property --
    never to re-parse or re-validate locations independently)
  - source_registry.py (list_sources(), for a sensible default source
    list -- never to call an adapter's search()/health_check())
"""

from dataclasses import dataclass, field

from candidate_profile import CandidateProfile, ProfileStatus
from location_taxonomy import LocationKind, normalize_location_text
from source_adapter import AdapterStatus
import source_registry


# score_job.py's own REJECT threshold (CLAUDE.md: "Reject = below 70")
# -- a property of the scoring rubric itself, not any one candidate's
# preference. Only used when the caller does not supply an explicit
# minimum_match_score.
DEFAULT_MINIMUM_MATCH_SCORE = 70

_REMOTE_KINDS = (
    LocationKind.REMOTE_UNSPECIFIED,
    LocationKind.REMOTE_COUNTRY,
    LocationKind.REMOTE_GLOBAL,
)


class SearchProfileError(ValueError):
    """Raised when a CandidateProfile cannot be turned into a valid,
    submittable SearchProfile."""


def _default_sources():
    """Every currently-registered, ENABLED, real (non-MOCK) adapter --
    never a hardcoded source name, and automatically includes future
    adapters once they are actually enabled (AdapterStatus.ENABLED) in
    source_registry.ADAPTERS. A REGISTERED-but-NOT_ENABLED adapter
    (e.g. a Phase 4 skeleton with no working implementation yet) is
    deliberately excluded -- a candidate's default search plan must
    never silently include a source that cannot actually be queried."""
    return [
        s for s in source_registry.list_sources()
        if s != "MOCK" and source_registry.get_adapter_status(s) == AdapterStatus.ENABLED
    ]


@dataclass
class SearchProfile:
    candidate_id: str
    target_roles: list
    excluded_roles: list
    target_locations: list
    work_models: list
    minimum_experience_years: float | None
    maximum_experience_years: float | None
    sources: list
    minimum_match_score: int
    maximum_results: int | None
    active: bool
    profile_version: int
    confirmed_by_user: bool
    max_job_age_days: int | None = None

    @property
    def has_remote_preference(self):
        """
        Derived, informational only -- never independently stored or
        settable. True if ANY target location classifies (via the
        existing location_taxonomy.normalize_location_text(), not a
        second taxonomy) as a remote-type location. target_locations
        remains the one canonical representation of where this
        candidate wants to work.
        """
        return any(
            normalize_location_text(loc).kind in _REMOTE_KINDS
            for loc in self.target_locations
        )


def build_search_profile(
    profile,
    active,
    sources=None,
    minimum_match_score=None,
    maximum_results=None,
    minimum_experience_years=None,
    maximum_experience_years=None,
    max_job_age_days=None,
    target_roles_override=None,
    target_locations_override=None,
    work_models_override=None,
):
    """
    Build a SearchProfile from a CandidateProfile.

    `active` must be supplied explicitly by the caller (typically
    resolved from the candidates.status DB column) -- this function has
    no database access and cannot determine it itself.

    target_roles_override / target_locations_override /
    work_models_override are optional, explicit overrides letting a
    caller build a SearchProfile for a *particular saved search*
    without mutating the candidate's own persisted
    job_preferences (added for Phase 9's multi-saved-search support:
    one candidate profile, many independent search criteria sets).
    None (the default) means "use profile.job_preferences exactly as
    before" -- zero behavior change for every existing caller.

    Requires profile.metadata.profile_status == CONFIRMED and
    confirmed_by_user == True. Raises SearchProfileError otherwise --
    there is no allow_draft-style override anywhere in this function;
    a DRAFT (or ARCHIVED) profile can never produce a SearchProfile.

    minimum_experience_years / maximum_experience_years are optional,
    explicit overrides for what experience range of JOBS to search for
    (distinct from the candidate's own total_experience_years, which
    experience_eligibility.py already uses independently to judge
    whether a given job matches this candidate). Never derived or
    inferred from the candidate's own experience -- if not supplied,
    both remain None, and downstream eligibility relies solely on the
    existing experience_eligibility.py logic.

    max_job_age_days is an optional, explicit DISCOVERY constraint: "do
    not retrieve jobs older than this many days," enforced at the
    source/adapter level wherever the source supports it (see
    naukri_adapter.py's freshness-filter mapping). Defaults to None
    (unrestricted -- the existing, pre-this-feature behavior) when the
    caller does not supply it; nothing here invents a default of its
    own. This is deliberately distinct from freshness.py's
    classify_freshness(), which remains a read-only, post-retrieval
    ranking/reporting attribute on jobs that were already fetched --
    max_job_age_days never feeds into or replaces that classification.
    """
    if not isinstance(profile, CandidateProfile):
        raise SearchProfileError(
            f"expected a CandidateProfile, got {type(profile).__name__}"
        )

    if profile.metadata.profile_status != ProfileStatus.CONFIRMED:
        raise SearchProfileError(
            f"cannot build a search profile from a "
            f"{profile.metadata.profile_status.value} profile -- only a "
            f"CONFIRMED profile may be searched"
        )

    if not profile.metadata.confirmed_by_user:
        raise SearchProfileError(
            "profile_status is CONFIRMED but confirmed_by_user is False "
            "-- refusing an internally inconsistent profile"
        )

    target_roles = (
        list(target_roles_override)
        if target_roles_override is not None
        else list(profile.job_preferences.target_roles)
    )
    target_locations = (
        list(target_locations_override)
        if target_locations_override is not None
        else list(profile.job_preferences.target_locations)
    )
    work_models = (
        list(work_models_override)
        if work_models_override is not None
        else list(profile.job_preferences.work_model_preferences)
    )

    if not target_roles:
        raise SearchProfileError(
            "candidate profile has no job_preferences.target_roles -- "
            "cannot build a search plan"
        )

    if not target_locations:
        raise SearchProfileError(
            "candidate profile has no job_preferences.target_locations -- "
            "cannot build a search plan"
        )

    resolved_sources = list(sources) if sources else _default_sources()

    if not resolved_sources:
        raise SearchProfileError(
            "no search sources available (none supplied, and no "
            "non-MOCK adapter is currently registered)"
        )

    return SearchProfile(
        candidate_id=profile.identity.candidate_id,
        target_roles=target_roles,
        excluded_roles=list(profile.job_preferences.excluded_roles),
        target_locations=target_locations,
        work_models=work_models,
        minimum_experience_years=minimum_experience_years,
        maximum_experience_years=maximum_experience_years,
        sources=resolved_sources,
        minimum_match_score=(
            minimum_match_score
            if minimum_match_score is not None
            else DEFAULT_MINIMUM_MATCH_SCORE
        ),
        maximum_results=maximum_results,
        active=bool(active),
        profile_version=profile.metadata.profile_version,
        confirmed_by_user=profile.metadata.confirmed_by_user,
        max_job_age_days=max_job_age_days,
    )
