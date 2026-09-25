"""
Phase 9 candidate/profile persistence -- a thin layer over the
EXISTING candidates / candidate_search_profile DB tables (added by
scripts/migrate_v2_schema.py) and the EXISTING candidate_profile.py
dataclasses/normalize/validate/serialize functions. No second profile
schema or serialization format is introduced here.
"""

import copy
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from candidate_profile import (
    CandidateProfile,
    Certification,
    Education,
    EmploymentEntry,
    Identity,
    JobPreferences,
    ProfessionalSummary,
    ProfileStatus,
    Provenance,
    Skill,
    SkillSet,
    SKILL_CATEGORIES,
    normalize_candidate_profile,
    promote_to_confirmed,
    serialize_candidate_profile,
    validate_candidate_profile,
)


class ProfileStoreError(ValueError):
    """Raised for any candidate/profile persistence failure (unknown
    candidate, no profile row, invalid profile shape)."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_candidate(conn, name, email=None, phone=None):
    candidate_id = "cand_" + uuid.uuid4().hex[:12]
    now = _now()

    conn.execute(
        """
        INSERT INTO candidates (candidate_id, name, email, phone, created_at, updated_at, status)
        VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE')
        """,
        (candidate_id, name, email, phone, now, now),
    )

    profile = CandidateProfile(
        identity=Identity(candidate_id=candidate_id, name=name, email=email, phone=phone),
    )
    profile.metadata.created_at = now
    profile.metadata.updated_at = now

    _insert_profile_version(conn, candidate_id, profile, version=1)
    conn.commit()
    return candidate_id


def get_candidate(conn, candidate_id):
    row = conn.execute(
        "SELECT candidate_id, name, email, phone, status, created_at, updated_at FROM candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise ProfileStoreError(f"Unknown candidate: {candidate_id!r}")
    return dict(row)


def _insert_profile_version(conn, candidate_id, profile, version, source_resume_id=None):
    profile_json = json.dumps(serialize_candidate_profile(profile))
    now = _now()

    conn.execute(
        "UPDATE candidate_search_profile SET is_active = 0 WHERE candidate_id = ? AND is_active = 1",
        (candidate_id,),
    )
    conn.execute(
        """
        INSERT INTO candidate_search_profile (
            candidate_id, version, search_mode, profile_json,
            confirmed_by_user, is_active, source_resume_id, created_at, updated_at
        )
        VALUES (?, ?, 'PROFILE', ?, ?, 1, ?, ?, ?)
        """,
        (
            candidate_id,
            version,
            profile_json,
            1 if profile.metadata.confirmed_by_user else 0,
            source_resume_id,
            now,
            now,
        ),
    )


def _next_version(conn, candidate_id):
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM candidate_search_profile WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    return (row[0] or 0) + 1


def load_active_profile(conn, candidate_id):
    get_candidate(conn, candidate_id)  # raises if unknown

    row = conn.execute(
        """
        SELECT profile_json FROM candidate_search_profile
        WHERE candidate_id = ? AND is_active = 1
        ORDER BY version DESC LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()

    if row is None:
        raise ProfileStoreError(f"No active profile for candidate {candidate_id!r}")

    raw = json.loads(row[0])
    return normalize_candidate_profile(raw)


def save_profile_as_new_version(conn, candidate_id, profile, source_resume_id=None):
    """
    source_resume_id: which resumes.resume_id (if any) produced THIS
    version -- the caller decides this explicitly (never guessed/
    inherited implicitly here). See migrate_v8_resume_profile_
    traceability.py.
    """
    version = _next_version(conn, candidate_id)
    _insert_profile_version(conn, candidate_id, profile, version, source_resume_id=source_resume_id)
    conn.commit()
    return version


def _current_source_resume_id(conn, candidate_id):
    """The currently-active version's own source_resume_id, for
    callers that must carry lineage forward across a save that isn't
    itself a fresh resume upload (confirm, manual edit)."""
    row = conn.execute(
        "SELECT source_resume_id FROM candidate_search_profile WHERE candidate_id = ? AND is_active = 1 ORDER BY version DESC LIMIT 1",
        (candidate_id,),
    ).fetchone()
    return row[0] if row else None


def load_profile_version(conn, candidate_id, version):
    """Load one SPECIFIC historical profile version (not necessarily
    the active one) -- used for viewing/scoring against a saved
    search's pinned profile_version. Raises ProfileStoreError if that
    version doesn't exist for this candidate."""
    get_candidate(conn, candidate_id)  # raises if unknown

    row = conn.execute(
        "SELECT profile_json FROM candidate_search_profile WHERE candidate_id = ? AND version = ?",
        (candidate_id, version),
    ).fetchone()
    if row is None:
        raise ProfileStoreError(f"No profile version {version!r} for candidate {candidate_id!r}")

    raw = json.loads(row[0])
    return normalize_candidate_profile(raw)


def list_profile_versions(conn, candidate_id):
    """Every profile version for this candidate, most recent first --
    with resume filename/uploaded_at joined in where source_resume_id
    is set, for the "Current profile / Resume v1 / v2 / ..." selector
    UI. Never fabricates a resume link for a version that doesn't have
    one (e.g. a manually-entered profile)."""
    get_candidate(conn, candidate_id)  # raises if unknown

    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    rows = cursor.execute(
        """
        SELECT csp.version, csp.confirmed_by_user, csp.is_active, csp.source_resume_id,
               csp.created_at, r.filename AS resume_filename, r.resume_version AS resume_version_label,
               r.uploaded_at AS resume_uploaded_at
        FROM candidate_search_profile csp
        LEFT JOIN resumes r ON r.resume_id = csp.source_resume_id
        WHERE csp.candidate_id = ?
        ORDER BY csp.version DESC
        """,
        (candidate_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def confirm_active_profile(conn, candidate_id):
    """Promote the candidate's current active (DRAFT) profile to
    CONFIRMED and persist it as a new version. Raises ValueError (from
    promote_to_confirmed's own validation) if it does not yet meet the
    CONFIRMED minimum -- candidate_profile.py's own rule, not
    reimplemented here."""
    profile = load_active_profile(conn, candidate_id)
    confirmed = promote_to_confirmed(profile)
    # Carries forward whichever resume the profile being confirmed
    # already traced back to -- confirming is not a new resume upload,
    # so lineage must not be lost or reset.
    source_resume_id = _current_source_resume_id(conn, candidate_id)
    save_profile_as_new_version(conn, candidate_id, confirmed, source_resume_id=source_resume_id)
    return confirmed


def _apply_skill_dict(skills_in):
    return SkillSet(
        **{
            category: [
                Skill(
                    name=s.name,
                    normalized_name=s.name.strip().lower(),
                    proficiency=s.proficiency,
                    years=s.years,
                )
                for s in (skills_in.get(category) or [])
            ]
            for category in SKILL_CATEGORIES
        }
    )


def apply_profile_update(profile, update):
    """
    Return a NEW CandidateProfile with `update`'s non-None fields
    applied over `profile`. Never mutates the input. Any edit resets
    profile_status to DRAFT / confirmed_by_user to False -- an edited
    profile is never silently treated as still-confirmed (the human
    must explicitly re-confirm via POST .../profile/confirm).
    """
    result = copy.deepcopy(profile)

    if update.name is not None:
        result.identity.name = update.name
    if update.email is not None:
        result.identity.email = update.email
    if update.phone is not None:
        result.identity.phone = update.phone

    if update.headline is not None:
        result.professional_summary.headline = update.headline
    if update.summary is not None:
        result.professional_summary.summary = update.summary
    if update.total_experience_years is not None:
        result.professional_summary.total_experience_years = update.total_experience_years
    if update.current_title is not None:
        result.professional_summary.current_title = update.current_title
    if update.seniority_level is not None:
        result.professional_summary.seniority_level = update.seniority_level
    if update.industries is not None:
        result.professional_summary.industries = list(update.industries)

    if update.skills is not None:
        result.skills = _apply_skill_dict(update.skills)

    if update.certifications is not None:
        result.certifications = [
            Certification(
                name=c.name,
                issuer=c.issuer,
                credential_id=c.credential_id,
                issued_date=c.issued_date,
                expiry_date=c.expiry_date,
            )
            for c in update.certifications
        ]

    if update.education is not None:
        result.education = [
            Education(
                institution=e.institution,
                degree=e.degree,
                field_of_study=e.field_of_study,
                start_date=e.start_date,
                end_date=e.end_date,
            )
            for e in update.education
        ]

    if update.employment_history is not None:
        result.employment_history = [
            EmploymentEntry(
                employer=emp.employer,
                title=emp.title,
                start_date=emp.start_date,
                end_date=emp.end_date,
                description=emp.description,
                skills=list(emp.skills),
            )
            for emp in update.employment_history
        ]

    if update.job_preferences is not None:
        jp = update.job_preferences
        result.job_preferences = JobPreferences(
            target_roles=list(jp.target_roles),
            excluded_roles=list(jp.excluded_roles),
            target_seniority=jp.target_seniority,
            target_locations=list(jp.target_locations),
            work_model_preferences=list(jp.work_model_preferences),
            employment_type=jp.employment_type,
            salary_expectation_min=jp.salary_expectation_min,
            salary_expectation_max=jp.salary_expectation_max,
            salary_currency=jp.salary_currency,
            relocation_preference=jp.relocation_preference,
        )

    result.metadata.profile_status = ProfileStatus.DRAFT
    result.metadata.confirmed_by_user = False
    result.metadata.updated_at = _now()

    return result


def save_resume_extracted_profile(conn, candidate_id, extracted_profile, resume_id=None):
    """Persist a fresh resume-extracted DRAFT profile as a new version,
    merged onto the candidate's existing identity where the resume left
    a field blank (a resume rarely repeats the email already on file,
    for instance) -- never overwrites an existing non-empty field with
    None.

    resume_id: the resumes.resume_id this profile was extracted FROM
    (see resume_store.record_uploaded_resume) -- recorded as this new
    version's source_resume_id for full traceability. None only for a
    caller that genuinely has no resume_id (should not happen via the
    real upload endpoint, which always records one first)."""
    current = None
    try:
        current = load_active_profile(conn, candidate_id)
    except ProfileStoreError:
        pass

    if current is not None:
        if not extracted_profile.identity.name and current.identity.name:
            extracted_profile.identity.name = current.identity.name
        if not extracted_profile.identity.email and current.identity.email:
            extracted_profile.identity.email = current.identity.email
        if not extracted_profile.identity.phone and current.identity.phone:
            extracted_profile.identity.phone = current.identity.phone

    extracted_profile.metadata.updated_at = _now()
    version = save_profile_as_new_version(conn, candidate_id, extracted_profile, source_resume_id=resume_id)
    return extracted_profile, version
