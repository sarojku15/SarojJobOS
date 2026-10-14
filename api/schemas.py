"""Pydantic request/response models for the Phase 9 API. Generic --
no field here is specific to any one job title, skill set, or location."""

from typing import Optional

from pydantic import BaseModel, Field


class CandidateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: Optional[str] = Field(None, max_length=254)
    phone: Optional[str] = Field(None, max_length=40)


class SkillIn(BaseModel):
    name: str
    proficiency: Optional[str] = None
    years: Optional[float] = None


class CertificationIn(BaseModel):
    name: str
    issuer: Optional[str] = None
    credential_id: Optional[str] = None
    issued_date: Optional[str] = None
    expiry_date: Optional[str] = None


class EducationIn(BaseModel):
    institution: str
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class EmploymentEntryIn(BaseModel):
    employer: str
    title: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    description: Optional[str] = None
    skills: list[str] = Field(default_factory=list)


class JobPreferencesIn(BaseModel):
    target_roles: list[str] = Field(default_factory=list)
    excluded_roles: list[str] = Field(default_factory=list)
    target_seniority: Optional[str] = None
    target_locations: list[str] = Field(default_factory=list)
    work_model_preferences: list[str] = Field(default_factory=list)
    employment_type: Optional[str] = None
    salary_expectation_min: Optional[float] = None
    salary_expectation_max: Optional[float] = None
    salary_currency: Optional[str] = None
    relocation_preference: Optional[str] = None


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    headline: Optional[str] = None
    summary: Optional[str] = None
    total_experience_years: Optional[float] = None
    current_title: Optional[str] = None
    seniority_level: Optional[str] = None
    industries: Optional[list[str]] = None
    skills: Optional[dict[str, list[SkillIn]]] = None
    certifications: Optional[list[CertificationIn]] = None
    education: Optional[list[EducationIn]] = None
    employment_history: Optional[list[EmploymentEntryIn]] = None
    job_preferences: Optional[JobPreferencesIn] = None


class ScheduleIn(BaseModel):
    # NOTE: this is the legacy, informational-only field on
    # SavedSearchCreate/Update (writes to saved_searches.schedule_json,
    # never activates anything). To actually enable recurring
    # execution, use PUT /api/searches/{id}/schedule (ScheduleSetIn
    # below), backed by the real search_schedules table + scripts/
    # scheduler.py -- see docs/ARCHITECTURE.md's "Scheduling" section.
    enabled: bool = False
    frequency: Optional[str] = None  # e.g. "DAILY" -- stored only, never activates a scheduler


class ScheduleSetIn(BaseModel):
    """The real scheduling control -- PUT /api/searches/{id}/schedule."""
    enabled: bool
    frequency: Optional[str] = None  # "hourly" / "daily" / "weekly" -- required if enabled=True
    timezone: str = "UTC"


class TailorResumeIn(BaseModel):
    base_resume_id: str
    job_id: str


class CompanyResearchIn(BaseModel):
    company_name: str = Field(..., min_length=1)
    job_id: Optional[str] = None


class InterviewPrepIn(BaseModel):
    job_id: str
    resume_id: Optional[str] = None
    company_research_id: Optional[str] = None


class InterviewAnswerIn(BaseModel):
    candidate_answer: Optional[str] = None
    confidence: Optional[int] = Field(None, ge=1, le=5)
    notes: Optional[str] = None


class InterviewOutcomeIn(BaseModel):
    outcome_status: Optional[str] = None
    outcome_notes: Optional[str] = None


class SavedSearchCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    target_roles: list[str] = Field(..., min_length=1)
    target_locations: list[str] = Field(..., min_length=1)
    work_models: list[str] = Field(default_factory=list)
    employment_type: Optional[str] = None
    minimum_experience_years: Optional[float] = Field(None, ge=0)
    maximum_experience_years: Optional[float] = Field(None, ge=0)
    salary_expectation_min: Optional[float] = Field(None, ge=0)
    salary_expectation_max: Optional[float] = Field(None, ge=0)
    salary_currency: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    minimum_match_score: Optional[int] = Field(None, ge=0, le=100)
    max_job_age_days: Optional[int] = Field(3, ge=1, le=365)
    sources: Optional[list[str]] = None
    schedule: Optional[ScheduleIn] = None
    # Pins this search to one specific historical
    # candidate_search_profile.version instead of always dynamically
    # using whichever profile is currently active -- None (default)
    # preserves today's existing "always current" behavior exactly.
    # See migrate_v8_resume_profile_traceability.py.
    profile_version: Optional[int] = None
    # None (default) = legacy scoring, exactly as before, using
    # this search's own minimum_match_score column above. When set,
    # this search's results use scripts/scoring_profiles.py's
    # weighted model instead -- see docs/SCORING.md's "Custom
    # Scoring Profiles" section for the precedence rule.
    scoring_profile_id: Optional[str] = None
    # USER (default, via the DB column's own DEFAULT when omitted) /
    # TEST / SYSTEM -- see migrate_v10_search_type.py. A real user
    # creating a search through the normal UI never sets this; only
    # this project's own test suite/live-verification scripts pass
    # "TEST" explicitly.
    search_type: Optional[str] = None


class SavedSearchUpdate(BaseModel):
    name: Optional[str] = None
    target_roles: Optional[list[str]] = None
    target_locations: Optional[list[str]] = None
    work_models: Optional[list[str]] = None
    employment_type: Optional[str] = None
    minimum_experience_years: Optional[float] = Field(None, ge=0)
    maximum_experience_years: Optional[float] = Field(None, ge=0)
    salary_expectation_min: Optional[float] = Field(None, ge=0)
    salary_expectation_max: Optional[float] = Field(None, ge=0)
    salary_currency: Optional[str] = None
    skills: Optional[list[str]] = None
    minimum_match_score: Optional[int] = Field(None, ge=0, le=100)
    max_job_age_days: Optional[int] = Field(None, ge=1, le=365)
    sources: Optional[list[str]] = None
    schedule: Optional[ScheduleIn] = None
    profile_version: Optional[int] = None
    scoring_profile_id: Optional[str] = None


class ManualJobImportIn(BaseModel):
    """Phase 13 broad-discovery, Part 13 -- a human-supplied job the
    candidate is already viewing in their own browser (see
    scripts/manual_import.py's docstring for why this is fields the
    human types/pastes, not a URL JobOS fetches server-side)."""

    job_source: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    company: str = Field(..., min_length=1)
    job_url: str = Field(..., min_length=1)
    location: Optional[str] = ""
    work_model: Optional[str] = ""
    application_url: Optional[str] = ""
    posted_date: Optional[str] = ""
    experience_required: Optional[str] = ""
    jd_text: Optional[str] = ""


class SearchProviderOrderIn(BaseModel):
    order: list[str] = Field(..., min_length=1)


class SearchProviderKeyIn(BaseModel):
    api_key: str = Field(..., min_length=1)


class SearchProviderResetUsageIn(BaseModel):
    window: Optional[str] = None  # "daily" | "monthly" | None (both)


class CandidatePatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    email: Optional[str] = Field(None, max_length=254)
    phone: Optional[str] = Field(None, max_length=40)


class JobStatusUpdate(BaseModel):
    status: str = Field(..., min_length=1, max_length=60)


class FollowUpDateIn(BaseModel):
    # None/omitted clears the follow-up date. A plain ISO date string
    # (e.g. "2026-10-05") -- never a datetime, this is a reminder date,
    # not a timestamp.
    follow_up_date: Optional[str] = None


class MarkAppliedIn(BaseModel):
    """The dedicated "Mark as Applied" action (2026-09-28 application-
    tracker implementation). Every field is optional so the endpoint
    still works as a minimal "just record APPLIED" call, but the
    frontend is expected to prompt for resume_id/resume_variant at the
    moment of applying rather than silently omitting them -- see
    api/main.py's mark_job_applied() docstring."""
    resume_id: Optional[str] = Field(None, max_length=200)
    resume_variant: Optional[str] = Field(None, max_length=200)
    # None = default to the server's own current time (see
    # application_lifecycle.mark_applied()). A caller-supplied value is
    # honored ONLY the first time applied_at is actually set -- never
    # used to move an already-recorded applied_at.
    applied_at: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=5000)
    # Optional convenience: schedule a follow-up in the same call as
    # marking applied, reusing the exact same follow-up mechanism a
    # separate PUT .../follow-up-schedule call would use.
    follow_up_date: Optional[str] = None


class ApplicationNotesIn(BaseModel):
    notes: Optional[str] = Field(None, max_length=5000)


class FollowUpScheduleIn(BaseModel):
    due_date: str = Field(..., min_length=1, max_length=20)
    notes: Optional[str] = Field(None, max_length=2000)


class FollowUpCompleteIn(BaseModel):
    notes: Optional[str] = Field(None, max_length=2000)


class ScoringProfileCreateIn(BaseModel):
    """Custom Job Scoring Profiles. `weights` keys must be one of the
    seven criteria scripts/scoring_profiles.py supports -- validated
    server-side (scoring_profile_store._validate_payload()), never
    trusted blindly. Weights do not need to already sum to 100 --
    they are normalized at evaluation/save time (see
    scoring_profiles.validate_scoring_profile())."""
    name: str = Field(..., min_length=1, max_length=200)
    weights: dict = Field(...)
    description: Optional[str] = Field(None, max_length=2000)
    mandatory_gate_enabled: bool = True
    min_shortlist_score: float = Field(70, ge=0, le=100)
    is_default: bool = False


class ScoringProfileUpdateIn(BaseModel):
    """Every field optional -- an update only touches the fields the
    caller actually supplied (same convention as CandidatePatch)."""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    weights: Optional[dict] = None
    description: Optional[str] = Field(None, max_length=2000)
    mandatory_gate_enabled: Optional[bool] = None
    min_shortlist_score: Optional[float] = Field(None, ge=0, le=100)
    is_default: Optional[bool] = None


class ScoringProfileDuplicateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class ScoringProfilePreviewIn(BaseModel):
    """Score ONE representative job against a (not-yet-saved, or
    already-saved) profile -- never runs a real search, never writes
    anything. `job` is a plain job dict in the same shape
    discover_local.normalize_job() produces (title/jd_text/location/
    work_model/mandatory_skills/salary_min/salary_max, all optional
    except title). `scoring_profile_id` is used INSTEAD of inline
    weights when previewing an already-saved profile."""
    job: dict = Field(...)
    scoring_profile_id: Optional[str] = None
    weights: Optional[dict] = None
    mandatory_gate_enabled: bool = True
    min_shortlist_score: float = Field(70, ge=0, le=100)
