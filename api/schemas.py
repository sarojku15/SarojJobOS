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
    enabled: bool = False
    frequency: Optional[str] = None  # e.g. "DAILY" -- stored only, never activates a scheduler


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
