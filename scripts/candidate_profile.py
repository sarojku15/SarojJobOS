#!/usr/bin/env python3

"""
Canonical candidate-profile schema and validation layer.

============================================================================
WHY THIS EXISTS
============================================================================
Every scoring/eligibility module in this project (score_job.py,
experience_eligibility.py, location_taxonomy.py, job_eligibility.py)
already takes an explicit `candidate_profile` argument -- there is no
global default for any specific candidate anywhere in that layer. What
has been missing until now is a CANONICAL, documented shape for that
argument that can hold everything a resume or a manual-entry form will
eventually need to supply, plus a validation layer and an explicit
DRAFT -> CONFIRMED
lifecycle so an unreviewed, machine-extracted profile can never be
silently treated as the authoritative one.

This module defines that canonical shape (CandidateProfile and its
nested sections), validates it, and projects it into the flat dict
shape the existing scoring/eligibility functions already consume
(to_legacy_matching_profile()) -- so NONE of those functions need to
change. There is exactly one canonical representation of experience,
target locations, work model, and target roles; the flat "legacy" dict
is a deterministic, lossy VIEW derived from the canonical profile every
time, never an independently-maintained second copy.

============================================================================
LIFECYCLE
============================================================================
ProfileStatus.DRAFT
    An incomplete, unreviewed profile. Created either by a user manually
    entering partial details, or (in a future component) by resume
    extraction. Tolerates missing almost everything -- see
    validate_candidate_profile() for the very small set of fields
    required even in DRAFT (candidate_id: a profile must belong to
    someone).

ProfileStatus.CONFIRMED
    The user has reviewed and confirmed the profile. This is the ONLY
    status to_legacy_matching_profile() will convert by default --
    calling it on a DRAFT profile raises ValueError unless the caller
    explicitly passes allow_draft=True (intended only for a future
    "preview my draft's matches" feature, never for real matching/
    ingestion). This is the structural guard against a draft profile
    being silently used as the authoritative one.

ProfileStatus.ARCHIVED
    A previously CONFIRMED (or DRAFT) profile that is no longer active
    -- e.g. superseded by a newer version. Never authoritative for
    matching; to_legacy_matching_profile() rejects it the same as DRAFT.

Promotion DRAFT -> CONFIRMED happens through promote_to_confirmed(),
which re-validates against the CONFIRMED-specific minimum requirements
before flipping profile_status and confirmed_by_user together (these two
fields are kept in lock-step deliberately -- see
validate_candidate_profile()'s consistency check). There is no
CONFIRMED -> DRAFT downgrade path exposed here (out of scope for this
component); archiving is a separate, simple field assignment left to a
future component that manages profile_version history.

============================================================================
PROVENANCE
============================================================================
Provenance.USER_ENTERED    -- the candidate (or an operator) typed it in.
Provenance.RESUME_EXTRACTED -- a future resume-parsing component filled
                                it in; not yet reviewed by the user.
Provenance.SYSTEM_NORMALIZED -- this project's own normalization logic
                                 derived it (e.g. a canonicalized skill
                                 name) rather than any human or resume
                                 text supplying it directly.

Provenance is attached per-Skill (the field most likely to be resume-
extracted at scale) and once at the whole-profile level in
ProfileMetadata.source. It is intentionally NOT attached to every single
field of Identity/Education/Employment -- that would be over-engineering
for a component that does not parse resumes yet. When resume extraction
is built, it can extend per-field provenance further; nothing here
prevents that.

============================================================================
MATCHING vs INFORMATIONAL FIELDS
============================================================================
Authoritative for matching today (consumed by score_job.py /
experience_eligibility.py / location_taxonomy.py / job_eligibility.py,
via to_legacy_matching_profile()):
    - professional_summary.total_experience_years
    - job_preferences.target_locations
    - skills.* (all ten categories, for score_job.py's role/skill
      keyword matching and evaluate_hard_reject()'s mandatory-skill
      check)

Informational only today (stored, validated, NOT read by any existing
scoring/eligibility function -- reserved for future components such as
resume review UI, cover-letter generation, or richer scoring):
    - identity.email / identity.phone
    - professional_summary.headline / summary / current_title /
      seniority_level
    - job_preferences.target_roles / excluded_roles / target_seniority /
      work_model_preferences / employment_type / salary_expectation_* /
      relocation_preference
    - certifications, education, employment_history (all of section
      E/F/G)

May legitimately be missing at any lifecycle stage: everything except
identity.candidate_id (see validate_candidate_profile()). Missing means
exactly that -- None or an empty list -- never guessed or defaulted to
a non-empty value.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from location_taxonomy import normalize_location_text, LocationKind


class ProfileStatus(Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    ARCHIVED = "ARCHIVED"


class Provenance(Enum):
    USER_ENTERED = "USER_ENTERED"
    RESUME_EXTRACTED = "RESUME_EXTRACTED"
    SYSTEM_NORMALIZED = "SYSTEM_NORMALIZED"


# The ten skill categories from this component's spec (section D),
# named to match config/profile.json's own existing top-level keys
# wherever a direct equivalent already exists, so
# to_legacy_matching_profile() is a plain renaming, not a remapping.
SKILL_CATEGORIES = (
    "cloud",
    "containers_orchestration",
    "infrastructure_iac",
    "cicd",
    "observability",
    "programming_scripting",
    "databases_storage",
    "security_iam",
    "incident_itsm",
    "other",
)

# Maps this module's canonical skill-category names to
# config/profile.json's existing key names, used only by
# to_legacy_matching_profile(). "databases_storage" and "incident_itsm"
# have no existing equivalent in config/profile.json and are folded
# into "tools" on export (score_job.py's mandatory-skill check just
# concatenates every category's text together, so nothing is lost --
# see to_legacy_matching_profile()'s docstring for the exact mapping).
_LEGACY_SKILL_KEY = {
    "cloud": "cloud",
    "containers_orchestration": "kubernetes",
    "infrastructure_iac": "iac_and_automation",
    "cicd": "cicd_and_devops",
    "observability": "observability",
    "programming_scripting": "programming_and_scripting",
    "security_iam": "sre",
    "incident_itsm": "sre",
    "databases_storage": "tools",
    "other": "tools",
}


@dataclass
class Identity:
    candidate_id: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None


@dataclass
class ProfessionalSummary:
    headline: str | None = None
    summary: str | None = None
    total_experience_years: float | None = None
    current_title: str | None = None
    seniority_level: str | None = None
    # Purely informational (Phase 10 GUI field), like
    # JobPreferences.work_model_preferences -- no existing scoring/
    # eligibility function reads it. A generic list of industries the
    # candidate has worked in/targets, applicable to any profession.
    industries: list = field(default_factory=list)


@dataclass
class Skill:
    name: str
    normalized_name: str | None = None
    proficiency: str | None = None
    years: float | None = None
    provenance: Provenance | None = None


@dataclass
class SkillSet:
    cloud: list = field(default_factory=list)
    containers_orchestration: list = field(default_factory=list)
    infrastructure_iac: list = field(default_factory=list)
    cicd: list = field(default_factory=list)
    observability: list = field(default_factory=list)
    programming_scripting: list = field(default_factory=list)
    databases_storage: list = field(default_factory=list)
    security_iam: list = field(default_factory=list)
    incident_itsm: list = field(default_factory=list)
    other: list = field(default_factory=list)

    def categories(self):
        return {name: getattr(self, name) for name in SKILL_CATEGORIES}


@dataclass
class Certification:
    name: str
    issuer: str | None = None
    credential_id: str | None = None
    issued_date: str | None = None
    expiry_date: str | None = None


@dataclass
class Education:
    institution: str
    degree: str | None = None
    field_of_study: str | None = None
    start_date: str | None = None
    end_date: str | None = None


@dataclass
class EmploymentEntry:
    employer: str
    title: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = None
    skills: list = field(default_factory=list)


@dataclass
class JobPreferences:
    """
    Sections C, H, and I of the component spec are deliberately merged
    into one dataclass rather than three: "target roles" and "target
    locations" are each a single canonical list here, referenced by
    matching logic directly -- there is no separate "target roles"
    section duplicating this one's target_roles field, and no separate
    location model duplicating target_locations.

    Remote preferences are NOT a separate field: per location_taxonomy
    .py's own model, "Remote", "Remote India", and "Remote Global" are
    simply entries within target_locations (exactly as
    config/profile.json already does today) -- adding a second,
    separate remote-preference field would be exactly the kind of
    competing representation this component is required to avoid.

    work_model_preferences is informational only today: no existing
    scoring/eligibility function reads a candidate's own work-model
    preference (score_job.py's Location/work-model dimension and
    location_taxonomy.py's work-model handling both operate on the
    JOB's work model, never the candidate's preference for one).
    """
    target_roles: list = field(default_factory=list)
    excluded_roles: list = field(default_factory=list)
    target_seniority: str | None = None
    target_locations: list = field(default_factory=list)
    work_model_preferences: list = field(default_factory=list)
    employment_type: str | None = None
    salary_expectation_min: float | None = None
    salary_expectation_max: float | None = None
    salary_currency: str | None = None
    relocation_preference: str | None = None


@dataclass
class ProfileMetadata:
    profile_version: int = 1
    profile_status: ProfileStatus = ProfileStatus.DRAFT
    confirmed_by_user: bool = False
    source: Provenance = Provenance.USER_ENTERED
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class CandidateProfile:
    identity: Identity
    professional_summary: ProfessionalSummary = field(default_factory=ProfessionalSummary)
    skills: SkillSet = field(default_factory=SkillSet)
    certifications: list = field(default_factory=list)
    education: list = field(default_factory=list)
    employment_history: list = field(default_factory=list)
    job_preferences: JobPreferences = field(default_factory=JobPreferences)
    metadata: ProfileMetadata = field(default_factory=ProfileMetadata)


@dataclass
class ValidationIssue:
    field: str
    message: str
    severity: str = "ERROR"  # "ERROR" or "WARNING"


@dataclass
class ValidationResult:
    valid: bool
    errors: list = field(default_factory=list)

    @property
    def error_issues(self):
        return [i for i in self.errors if i.severity == "ERROR"]

    @property
    def warning_issues(self):
        return [i for i in self.errors if i.severity == "WARNING"]


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_provenance(value, field_name):
    if value is None:
        return None
    if isinstance(value, Provenance):
        return value
    try:
        return Provenance(value)
    except ValueError:
        raise ValueError(f"Invalid provenance for {field_name}: {value!r}")


def _parse_skill(raw_skill, category):
    if not isinstance(raw_skill, dict):
        raise ValueError(
            f"skills.{category} entry must be an object, got "
            f"{type(raw_skill).__name__}"
        )

    name = raw_skill.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"skills.{category} entry missing a non-empty 'name'")

    normalized_name = raw_skill.get("normalized_name")
    if normalized_name is None:
        # Pure mechanical normalization (lowercase/trim) -- never an
        # inferred or guessed canonical name.
        normalized_name = name.strip().lower()

    return Skill(
        name=name,
        normalized_name=normalized_name,
        proficiency=raw_skill.get("proficiency"),
        years=raw_skill.get("years"),
        provenance=_parse_provenance(
            raw_skill.get("provenance"), f"skills.{category}.provenance"
        ),
    )


def _parse_skill_list(raw_list, category):
    if raw_list is None:
        return []
    if not isinstance(raw_list, list):
        raise ValueError(f"skills.{category} must be a list")
    return [_parse_skill(item, category) for item in raw_list]


def normalize_candidate_profile(raw):
    """
    Convert a raw, JSON-deserializable dict into a CandidateProfile.

    Pure structural conversion: fills in empty-list/None defaults for
    genuinely absent optional fields, computes a mechanical
    normalized_name for each skill (lowercase/trim -- nothing inferred),
    and raises ValueError on structurally invalid input (wrong type,
    unparseable enum value, missing candidate_id). It never invents a
    value for a field the input did not supply, and it does not perform
    the semantic checks validate_candidate_profile() does (e.g. it will
    happily construct a DRAFT missing every optional field).
    """
    if not isinstance(raw, dict):
        raise ValueError("candidate profile must be a dict")

    identity_raw = raw.get("identity") or {}
    candidate_id = identity_raw.get("candidate_id")

    if not candidate_id or not isinstance(candidate_id, str):
        raise ValueError(
            "identity.candidate_id is required and must be a non-empty string"
        )

    identity = Identity(
        candidate_id=candidate_id,
        name=identity_raw.get("name"),
        email=identity_raw.get("email"),
        phone=identity_raw.get("phone"),
    )

    summary_raw = raw.get("professional_summary") or {}
    professional_summary = ProfessionalSummary(
        headline=summary_raw.get("headline"),
        summary=summary_raw.get("summary"),
        total_experience_years=summary_raw.get("total_experience_years"),
        current_title=summary_raw.get("current_title"),
        seniority_level=summary_raw.get("seniority_level"),
        industries=list(summary_raw.get("industries") or []),
    )

    skills_raw = raw.get("skills") or {}
    skills = SkillSet(
        **{
            category: _parse_skill_list(skills_raw.get(category), category)
            for category in SKILL_CATEGORIES
        }
    )

    certifications = []
    for c in raw.get("certifications") or []:
        if not isinstance(c, dict) or not c.get("name"):
            raise ValueError("Each certification requires a non-empty 'name'")
        certifications.append(
            Certification(
                name=c["name"],
                issuer=c.get("issuer"),
                credential_id=c.get("credential_id"),
                issued_date=c.get("issued_date"),
                expiry_date=c.get("expiry_date"),
            )
        )

    education = []
    for e in raw.get("education") or []:
        if not isinstance(e, dict) or not e.get("institution"):
            raise ValueError("Each education entry requires a non-empty 'institution'")
        education.append(
            Education(
                institution=e["institution"],
                degree=e.get("degree"),
                field_of_study=e.get("field_of_study"),
                start_date=e.get("start_date"),
                end_date=e.get("end_date"),
            )
        )

    employment_history = []
    for emp in raw.get("employment_history") or []:
        if not isinstance(emp, dict) or not emp.get("employer"):
            raise ValueError("Each employment_history entry requires a non-empty 'employer'")
        employment_history.append(
            EmploymentEntry(
                employer=emp["employer"],
                title=emp.get("title"),
                start_date=emp.get("start_date"),
                end_date=emp.get("end_date"),
                description=emp.get("description"),
                skills=list(emp.get("skills") or []),
            )
        )

    prefs_raw = raw.get("job_preferences") or {}
    job_preferences = JobPreferences(
        target_roles=list(prefs_raw.get("target_roles") or []),
        excluded_roles=list(prefs_raw.get("excluded_roles") or []),
        target_seniority=prefs_raw.get("target_seniority"),
        target_locations=list(prefs_raw.get("target_locations") or []),
        work_model_preferences=list(prefs_raw.get("work_model_preferences") or []),
        employment_type=prefs_raw.get("employment_type"),
        salary_expectation_min=prefs_raw.get("salary_expectation_min"),
        salary_expectation_max=prefs_raw.get("salary_expectation_max"),
        salary_currency=prefs_raw.get("salary_currency"),
        relocation_preference=prefs_raw.get("relocation_preference"),
    )

    meta_raw = raw.get("metadata") or {}

    status_value = meta_raw.get("profile_status", ProfileStatus.DRAFT.value)
    if isinstance(status_value, ProfileStatus):
        profile_status = status_value
    else:
        try:
            profile_status = ProfileStatus(status_value)
        except ValueError:
            raise ValueError(f"Invalid metadata.profile_status: {status_value!r}")

    source_value = meta_raw.get("source", Provenance.USER_ENTERED.value)
    if isinstance(source_value, Provenance):
        source = source_value
    else:
        try:
            source = Provenance(source_value)
        except ValueError:
            raise ValueError(f"Invalid metadata.source: {source_value!r}")

    metadata = ProfileMetadata(
        profile_version=meta_raw.get("profile_version", 1),
        profile_status=profile_status,
        confirmed_by_user=bool(meta_raw.get("confirmed_by_user", False)),
        source=source,
        created_at=meta_raw.get("created_at"),
        updated_at=meta_raw.get("updated_at"),
    )

    return CandidateProfile(
        identity=identity,
        professional_summary=professional_summary,
        skills=skills,
        certifications=certifications,
        education=education,
        employment_history=employment_history,
        job_preferences=job_preferences,
        metadata=metadata,
    )


def validate_candidate_profile(profile):
    """
    Semantic validation of an already-constructed CandidateProfile.

    Returns a ValidationResult whose `valid` is True iff there are no
    ERROR-severity issues (WARNING-severity issues -- e.g. a target
    location this taxonomy doesn't recognize, or a duplicate skill --
    do not invalidate the profile, but are reported so a review UI can
    surface them). A DRAFT profile only requires identity.candidate_id;
    a CONFIRMED profile additionally requires identity.name and
    professional_summary.total_experience_years -- the minimum genuinely
    needed by the existing matching pipeline (see the module docstring's
    "MATCHING vs INFORMATIONAL FIELDS" section) -- nothing more.
    """
    errors = []

    def err(field_name, message):
        errors.append(ValidationIssue(field=field_name, message=message, severity="ERROR"))

    def warn(field_name, message):
        errors.append(ValidationIssue(field=field_name, message=message, severity="WARNING"))

    if not isinstance(profile, CandidateProfile):
        err("<root>", f"expected a CandidateProfile instance, got {type(profile).__name__}")
        return ValidationResult(valid=False, errors=errors)

    if not profile.identity.candidate_id or not isinstance(profile.identity.candidate_id, str):
        err("identity.candidate_id", "candidate_id is required and must be a non-empty string")

    if not isinstance(profile.metadata.profile_status, ProfileStatus):
        err("metadata.profile_status", f"invalid lifecycle state: {profile.metadata.profile_status!r}")
    else:
        if profile.metadata.profile_status == ProfileStatus.CONFIRMED and not profile.metadata.confirmed_by_user:
            err(
                "metadata.confirmed_by_user",
                "profile_status is CONFIRMED but confirmed_by_user is False -- these must move together",
            )
        if profile.metadata.profile_status == ProfileStatus.DRAFT and profile.metadata.confirmed_by_user:
            err(
                "metadata.confirmed_by_user",
                "profile_status is DRAFT but confirmed_by_user is True -- these must move together",
            )

    years = profile.professional_summary.total_experience_years
    if years is not None:
        if not isinstance(years, (int, float)) or isinstance(years, bool):
            err(
                "professional_summary.total_experience_years",
                f"must be a number, got {type(years).__name__}",
            )
        elif years < 0:
            err(
                "professional_summary.total_experience_years",
                f"must be non-negative, got {years}",
            )

    if isinstance(profile.metadata.profile_status, ProfileStatus) and profile.metadata.profile_status == ProfileStatus.CONFIRMED:
        if not profile.identity.name:
            err("identity.name", "name is required for a CONFIRMED profile")
        if years is None:
            err(
                "professional_summary.total_experience_years",
                "total_experience_years is required for a CONFIRMED profile",
            )

    locations = profile.job_preferences.target_locations
    if not isinstance(locations, list):
        err("job_preferences.target_locations", f"must be a list, got {type(locations).__name__}")
    else:
        for loc in locations:
            if not isinstance(loc, str) or not loc.strip():
                err(
                    "job_preferences.target_locations",
                    f"each target location must be a non-empty string, got {loc!r}",
                )
            else:
                normalized = normalize_location_text(loc)
                if normalized.kind == LocationKind.UNKNOWN:
                    warn(
                        "job_preferences.target_locations",
                        f"'{loc}' is not recognized by the location taxonomy -- "
                        f"will be treated as UNKNOWN during matching, not rejected",
                    )

    for category in SKILL_CATEGORIES:
        skill_list = getattr(profile.skills, category)

        if not isinstance(skill_list, list):
            err(f"skills.{category}", f"must be a list, got {type(skill_list).__name__}")
            continue

        seen_normalized = {}

        for skill in skill_list:
            if not isinstance(skill, Skill):
                err(f"skills.{category}", f"each entry must be a Skill, got {type(skill).__name__}")
                continue

            if not skill.name or not isinstance(skill.name, str):
                err(f"skills.{category}", "skill entry missing a non-empty 'name'")
                continue

            if skill.years is not None:
                if not isinstance(skill.years, (int, float)) or isinstance(skill.years, bool) or skill.years < 0:
                    err(
                        f"skills.{category}",
                        f"skill '{skill.name}' years must be a non-negative number, got {skill.years!r}",
                    )

            if skill.proficiency is not None and not isinstance(skill.proficiency, str):
                err(
                    f"skills.{category}",
                    f"skill '{skill.name}' proficiency must be a string, got {type(skill.proficiency).__name__}",
                )

            key = skill.normalized_name or skill.name.strip().lower()

            if key in seen_normalized:
                warn(
                    f"skills.{category}",
                    f"duplicate skill '{skill.name}' (also listed as "
                    f"'{seen_normalized[key]}') -- consider merging before confirming",
                )
            else:
                seen_normalized[key] = skill.name

    for i, cert in enumerate(profile.certifications):
        if not isinstance(cert, Certification):
            err(f"certifications[{i}]", f"must be a Certification, got {type(cert).__name__}")
        elif not cert.name:
            err(f"certifications[{i}]", "certification missing a non-empty 'name'")

    for i, edu in enumerate(profile.education):
        if not isinstance(edu, Education):
            err(f"education[{i}]", f"must be an Education, got {type(edu).__name__}")
        elif not edu.institution:
            err(f"education[{i}]", "education entry missing a non-empty 'institution'")

    for i, emp in enumerate(profile.employment_history):
        if not isinstance(emp, EmploymentEntry):
            err(f"employment_history[{i}]", f"must be an EmploymentEntry, got {type(emp).__name__}")
        elif not emp.employer:
            err(f"employment_history[{i}]", "employment entry missing a non-empty 'employer'")

    salary_min = profile.job_preferences.salary_expectation_min
    salary_max = profile.job_preferences.salary_expectation_max
    if salary_min is not None and salary_max is not None:
        if not isinstance(salary_min, (int, float)) or not isinstance(salary_max, (int, float)):
            err(
                "job_preferences.salary_expectation_min/max",
                "salary_expectation_min/max must be numbers when present",
            )
        elif salary_min > salary_max:
            err(
                "job_preferences.salary_expectation_min/max",
                f"salary_expectation_min ({salary_min}) exceeds salary_expectation_max ({salary_max})",
            )

    valid = not any(issue.severity == "ERROR" for issue in errors)
    return ValidationResult(valid=valid, errors=errors)


def to_legacy_matching_profile(profile, allow_draft=False):
    """
    Project a CandidateProfile into the flat dict shape score_job.py,
    experience_eligibility.py, and location_taxonomy.py already
    consume -- the same shape config/profile.json has always had:

        {
          "candidate": {"experience_years": <float>},
          "target_locations": [<str>, ...],
          "cloud": [<str>, ...], "kubernetes": [<str>, ...],
          "iac_and_automation": [<str>, ...], "cicd_and_devops": [...],
          "observability": [...], "sre": [...],
          "programming_and_scripting": [...], "tools": [...],
        }

    This is a pure, deterministic, one-way projection -- it is
    regenerated from the canonical CandidateProfile every time, never
    edited independently, so there is exactly one source of truth.

    Refuses to convert a non-CONFIRMED profile by default: this is the
    structural guard against an unreviewed DRAFT (or an ARCHIVED
    profile) being silently used as the authoritative matching profile.
    Pass allow_draft=True only for an explicit "preview my draft's
    matches" feature -- never for real scoring/ingestion.
    """
    if profile.metadata.profile_status != ProfileStatus.CONFIRMED and not allow_draft:
        raise ValueError(
            f"Refusing to build a matching profile from a "
            f"{profile.metadata.profile_status.value} profile -- only a "
            f"CONFIRMED profile may be used for matching (pass "
            f"allow_draft=True only for an explicit preview feature, "
            f"never for real scoring/ingestion)."
        )

    legacy_skills = {legacy_key: [] for legacy_key in set(_LEGACY_SKILL_KEY.values())}

    for category in SKILL_CATEGORIES:
        legacy_key = _LEGACY_SKILL_KEY[category]
        for skill in getattr(profile.skills, category):
            legacy_skills[legacy_key].append(skill.name)

    return {
        "candidate": {
            "name": profile.identity.name,
            "experience_years": profile.professional_summary.total_experience_years,
        },
        "target_locations": list(profile.job_preferences.target_locations),
        # THIS candidate's own excluded roles/titles -- see score_job.py's
        # evaluate_hard_reject(), which used to read a single global
        # config/searches.json (one candidate's own exclude list applied
        # to every candidate). Empty by default, exactly like every
        # other optional preference here -- never a fabricated fallback.
        "excluded_roles": list(profile.job_preferences.excluded_roles),
        # THIS candidate's own target titles -- see score_job.py's
        # "Core role alignment" dimension, which used to check a
        # hardcoded, SRE/DevOps-specific title-keyword list against
        # every candidate regardless of what role they're actually
        # targeting.
        "target_roles": list(profile.job_preferences.target_roles),
        **legacy_skills,
    }


def promote_to_confirmed(profile):
    """
    Validate `profile` against CONFIRMED-level minimum requirements and,
    if it passes, return a NEW CandidateProfile with
    profile_status=CONFIRMED and confirmed_by_user=True set together
    (never independently -- see validate_candidate_profile()'s
    consistency check). Raises ValueError (with every validation error
    message) if the profile does not yet meet the CONFIRMED minimum.
    Does not mutate the input profile.
    """
    import copy

    candidate = copy.deepcopy(profile)
    candidate.metadata.profile_status = ProfileStatus.CONFIRMED
    candidate.metadata.confirmed_by_user = True
    candidate.metadata.updated_at = _now_iso()

    result = validate_candidate_profile(candidate)

    if not result.valid:
        messages = "; ".join(f"{i.field}: {i.message}" for i in result.error_issues)
        raise ValueError(f"Cannot confirm profile -- validation failed: {messages}")

    return candidate


def serialize_candidate_profile(profile):
    """
    Convert a CandidateProfile back into a plain, JSON-serializable
    dict (enums as their .value strings) -- the inverse of
    normalize_candidate_profile(). Intended for the eventual
    candidate_search_profile.profile_json storage column already
    present in the v2 database schema (scripts/migrate_v2_schema.py) --
    this function does not open or write to any database itself.
    """

    def skill_to_dict(s):
        return {
            "name": s.name,
            "normalized_name": s.normalized_name,
            "proficiency": s.proficiency,
            "years": s.years,
            "provenance": s.provenance.value if s.provenance else None,
        }

    return {
        "identity": {
            "candidate_id": profile.identity.candidate_id,
            "name": profile.identity.name,
            "email": profile.identity.email,
            "phone": profile.identity.phone,
        },
        "professional_summary": {
            "headline": profile.professional_summary.headline,
            "summary": profile.professional_summary.summary,
            "total_experience_years": profile.professional_summary.total_experience_years,
            "current_title": profile.professional_summary.current_title,
            "seniority_level": profile.professional_summary.seniority_level,
            "industries": list(profile.professional_summary.industries),
        },
        "skills": {
            category: [skill_to_dict(s) for s in getattr(profile.skills, category)]
            for category in SKILL_CATEGORIES
        },
        "certifications": [
            {
                "name": c.name,
                "issuer": c.issuer,
                "credential_id": c.credential_id,
                "issued_date": c.issued_date,
                "expiry_date": c.expiry_date,
            }
            for c in profile.certifications
        ],
        "education": [
            {
                "institution": e.institution,
                "degree": e.degree,
                "field_of_study": e.field_of_study,
                "start_date": e.start_date,
                "end_date": e.end_date,
            }
            for e in profile.education
        ],
        "employment_history": [
            {
                "employer": emp.employer,
                "title": emp.title,
                "start_date": emp.start_date,
                "end_date": emp.end_date,
                "description": emp.description,
                "skills": list(emp.skills),
            }
            for emp in profile.employment_history
        ],
        "job_preferences": {
            "target_roles": list(profile.job_preferences.target_roles),
            "excluded_roles": list(profile.job_preferences.excluded_roles),
            "target_seniority": profile.job_preferences.target_seniority,
            "target_locations": list(profile.job_preferences.target_locations),
            "work_model_preferences": list(profile.job_preferences.work_model_preferences),
            "employment_type": profile.job_preferences.employment_type,
            "salary_expectation_min": profile.job_preferences.salary_expectation_min,
            "salary_expectation_max": profile.job_preferences.salary_expectation_max,
            "salary_currency": profile.job_preferences.salary_currency,
            "relocation_preference": profile.job_preferences.relocation_preference,
        },
        "metadata": {
            "profile_version": profile.metadata.profile_version,
            "profile_status": profile.metadata.profile_status.value,
            "confirmed_by_user": profile.metadata.confirmed_by_user,
            "source": profile.metadata.source.value,
            "created_at": profile.metadata.created_at,
            "updated_at": profile.metadata.updated_at,
        },
    }
