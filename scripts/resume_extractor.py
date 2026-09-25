#!/usr/bin/env python3

"""
Resume PDF -> candidate-profile DRAFT extraction.

Produces a scripts/candidate_profile.py CandidateProfile with
profile_status=DRAFT, metadata.source=Provenance.RESUME_EXTRACTED, and
every extracted Skill stamped provenance=Provenance.RESUME_EXTRACTED.

============================================================================
NEVER-INVENT DISCIPLINE
============================================================================
This module extracts only what is literally, structurally present in the
resume text. It does NOT:
  - infer target_locations from the resume's own address/contact line
    (design principle #9 -- "where I currently live" is not "where I
    want to work"; target_locations is a user-declared job preference,
    populated later during manual review, never guessed from a resume)
  - infer seniority_level from a job title (e.g. "Senior..." does not
    populate seniority_level -- that field stays unset unless a resume
    literally labels it)
  - compute total_experience_years by summing employment date ranges
    (overlapping roles, employment gaps, and omitted early-career roles
    all make that arithmetic unreliable); it is populated ONLY from an
    explicit "N years" / "N+ years of experience" phrase in the
    resume's own summary text, and left None otherwise
  - split a single certification bullet that lists multiple
    certifications into several Certification entries (delimiter
    semantics are ambiguous and resume-specific; splitting could easily
    mis-attribute an issuer or misplace a credential ID) -- one bullet
    under CERTIFICATIONS becomes exactly one Certification, verbatim
  - populate any field this module cannot find a literal textual basis
    for; every such field is left None / empty, exactly as
    candidate_profile.py's DRAFT lifecycle expects

This is a generic resume parser, not tuned to any one candidate's
resume: section-header recognition uses a small set of common resume
header synonyms (SUMMARY/PROFILE, SKILLS, EXPERIENCE,
CERTIFICATIONS, EDUCATION), and skill-category classification uses
keyword matching against category LABELS the resume itself provides
(e.g. a "Cloud Platforms" heading contains "cloud" -> canonical
category "cloud") -- never against any specific candidate's skill
names. A resume section this module does not recognize (e.g. "KEY
PROJECTS", "ADDITIONAL EXPERTISE") is simply not extracted into any
structured field -- it is not part of candidate_profile.py's current
canonical schema, and no attempt is made to force it somewhere it does
not belong.

============================================================================
OUTPUT
============================================================================
extract_candidate_profile_draft(pdf_path, candidate_id) returns a bare
CandidateProfile with profile_status=DRAFT. It is never promoted to
CONFIRMED here -- per candidate_profile.py's own lifecycle rules, only
an explicit user review/edit/confirm step (a future component) may do
that, via candidate_profile.promote_to_confirmed().

extract_candidate_profile_draft_result(pdf_path, candidate_id) wraps
the same extraction in an explicit ExtractionResult -- status
(SUCCESS/FAILED), the resulting profile (None on FAILED), a
validate_candidate_profile() ValidationResult, and separated
warnings/errors -- without changing extract_candidate_profile_draft()
or any parsing logic above it. It carries no confidence score: this
extractor computes no defensible confidence value for any field, and
fabricating one would violate the same never-invent principle as
fabricating a resume field itself.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from candidate_profile import (
    Provenance,
    normalize_candidate_profile,
    validate_candidate_profile,
)


# ---------------------------------------------------------------------
# PDF -> raw text
# ---------------------------------------------------------------------

def extract_text_from_pdf(pdf_path):
    """
    Extract raw text from a resume PDF using pypdf. Returns the
    concatenated text of every page, page breaks joined by a newline.
    Raises FileNotFoundError / pypdf's own exceptions unmodified on a
    missing or unreadable file -- no silent fallback to empty text.
    """
    import pypdf

    reader = pypdf.PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# ---------------------------------------------------------------------
# Section splitting
# ---------------------------------------------------------------------

# Generic resume section-header synonyms -- common across resumes,
# specific to no one candidate. A section this module does not
# recognize (e.g. "KEY PROJECTS") is simply skipped; nothing forces its
# content into an unrelated field.
_SECTION_HEADER_ALIASES = {
    "summary": [
        "PROFESSIONAL SUMMARY", "SUMMARY", "PROFILE", "CAREER SUMMARY",
        "OBJECTIVE", "EXECUTIVE SUMMARY",
    ],
    "skills": [
        "TECHNICAL SKILLS", "SKILLS", "CORE COMPETENCIES", "KEY SKILLS",
        "SKILL SET",
    ],
    "experience": [
        "PROFESSIONAL EXPERIENCE", "WORK EXPERIENCE", "EMPLOYMENT HISTORY",
        "EXPERIENCE", "CAREER HISTORY",
    ],
    "certifications": [
        "CERTIFICATIONS", "CERTIFICATION", "LICENSES & CERTIFICATIONS",
        "LICENSES AND CERTIFICATIONS",
    ],
    "education": [
        "EDUCATION", "ACADEMIC QUALIFICATIONS", "EDUCATIONAL QUALIFICATIONS",
        "EDUCATION & TRAINING",
    ],
}

_HEADER_TO_SECTION = {
    alias: section
    for section, aliases in _SECTION_HEADER_ALIASES.items()
    for alias in aliases
}


def _looks_like_unrecognized_section_header(line):
    """
    Heuristic boundary detector for a resume section this module does
    NOT have a canonical field for (e.g. "KEY PROJECTS", "ADDITIONAL
    EXPERTISE", "SITE RELIABILITY ENGINEERING EXPERTISE"): a short,
    all-uppercase line. Without this, such a header would not be
    recognized as a boundary at all, and its content would silently
    leak into and corrupt whichever recognized section preceded it
    (e.g. an unrelated "KEY PROJECTS" bullet getting appended to the
    most recent employer's description). Deliberately NOT applied while
    still in the resume's preamble (see _split_into_sections()) --
    a candidate's own name is very often itself written in all caps.
    """
    letters = [c for c in line if c.isalpha()]
    if not letters or not all(c.isupper() for c in letters):
        return False
    if len(line) > 60:
        return False
    word_count = len(line.split())
    return 1 <= word_count <= 6


def _split_into_sections(text):
    """
    Split resume text into {section_key: [lines]} for the sections this
    module recognizes, plus a special "_preamble" entry holding every
    line before the first recognized header (name/contact/links).
    Lines belonging to an unrecognized section (e.g. "KEY PROJECTS")
    are routed to "_unrecognized" and dropped from the structured
    result -- see module docstring -- rather than being left attached
    to whatever recognized section came before them.
    """
    lines = [line.strip() for line in text.splitlines()]

    sections = {"_preamble": []}
    current = "_preamble"

    for line in lines:
        stripped = line.strip()

        if not stripped:
            continue

        header_key = stripped.upper().rstrip(":").strip()

        if header_key in _HEADER_TO_SECTION:
            current = _HEADER_TO_SECTION[header_key]
            sections.setdefault(current, [])
            continue

        if current != "_preamble" and _looks_like_unrecognized_section_header(stripped):
            current = "_unrecognized"
            sections.setdefault(current, [])
            continue

        sections.setdefault(current, []).append(stripped)

    return sections


# ---------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------

_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_PATTERN = re.compile(r"[+(]?\d[\d\-\s().]{6,}\d")


def _extract_identity(preamble_lines):
    name = preamble_lines[0] if preamble_lines else None

    email = None
    phone = None

    for line in preamble_lines[1:]:
        if email is None:
            email_match = _EMAIL_PATTERN.search(line)
            if email_match:
                email = email_match.group(0)
        if phone is None:
            phone_match = _PHONE_PATTERN.search(line)
            if phone_match:
                phone = phone_match.group(0).strip()

    return {"name": name, "email": email, "phone": phone}


# ---------------------------------------------------------------------
# Professional summary / total experience
# ---------------------------------------------------------------------

_EXPERIENCE_YEARS_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*\+?\s*years?\s+of\s+experience", re.IGNORECASE
)


def _extract_professional_summary(summary_lines):
    summary_text = " ".join(summary_lines).strip() or None

    total_experience_years = None
    if summary_text:
        match = _EXPERIENCE_YEARS_PATTERN.search(summary_text)
        if match:
            total_experience_years = float(match.group(1))

    return {
        "summary": summary_text,
        "total_experience_years": total_experience_years,
    }


# ---------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------

_SKILL_CATEGORY_KEYWORDS = {
    "cloud": ["cloud"],
    "containers_orchestration": ["container", "orchestration"],
    "infrastructure_iac": ["infrastructure as code", " iac", "iac ", "provisioning"],
    "cicd": ["ci/cd", "cicd", "continuous integration", "continuous delivery", "continuous deployment"],
    "observability": ["monitoring", "observability", "logging"],
    "programming_scripting": ["programming", "scripting", "languages"],
    "databases_storage": ["database", "storage", "data store"],
    "security_iam": ["security", "iam", "identity", "compliance"],
    "incident_itsm": ["itsm", "incident", "ticketing", "service management"],
}


def _classify_skill_category(label):
    lowered = f" {label.lower()} "
    for category, keywords in _SKILL_CATEGORY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return category
    return "other"


def _split_top_level(text, separator=","):
    """Split on `separator` while respecting parenthesis nesting, so
    "AWS (EC2, EKS, S3)" isn't broken apart at the commas inside the
    parentheses."""
    parts = []
    depth = 0
    current = []

    for char in text:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth = max(0, depth - 1)
            current.append(char)
        elif char == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)

    if current:
        parts.append("".join(current))

    return [p.strip() for p in parts if p.strip()]


_PAREN_CONTENT_PATTERN = re.compile(r"\(([^()]*)\)")


def _expand_skill_item(item):
    """
    "AWS (EC2, EKS, S3)" -> ["AWS", "EC2", "EKS", "S3"] -- every name is
    literally present in the resume text; nothing here is invented,
    only unpacked from the parenthetical list the resume itself gives.
    """
    match = _PAREN_CONTENT_PATTERN.search(item)
    base = _PAREN_CONTENT_PATTERN.sub("", item).strip().rstrip(",").strip()

    names = [base] if base else []

    if match:
        names.extend(s.strip() for s in match.group(1).split(",") if s.strip())

    return names or [item.strip()]


def _extract_skills(skill_lines):
    skills_by_category = {}

    for line in skill_lines:
        if ":" not in line:
            continue

        label, _, content = line.partition(":")
        category = _classify_skill_category(label)

        items = []
        for raw_item in _split_top_level(content):
            items.extend(_expand_skill_item(raw_item))

        skill_entries = [
            {"name": item, "provenance": Provenance.RESUME_EXTRACTED.value}
            for item in items
        ]

        skills_by_category.setdefault(category, []).extend(skill_entries)

    return skills_by_category


# ---------------------------------------------------------------------
# Employment history
# ---------------------------------------------------------------------

_MONTH_TOKEN = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
_DATE_RANGE_PATTERN = re.compile(
    rf"^(.*?)\s+({_MONTH_TOKEN}\s+\d{{4}}\s*[–\-]\s*(?:Present|{_MONTH_TOKEN}\s+\d{{4}}))\s*$"
)


def _extract_employment_history(experience_lines, known_skill_names):
    entries = []
    current = None

    for line in experience_lines:
        date_match = _DATE_RANGE_PATTERN.match(line)

        if date_match:
            if current:
                entries.append(current)

            title = date_match.group(1).strip() or None
            date_range = date_match.group(2)
            start_date, _, end_date = date_range.partition("–")
            if "-" in date_range and "–" not in date_range:
                start_date, _, end_date = date_range.partition("-")

            current = {
                "employer": None,
                "title": title,
                "start_date": start_date.strip() or None,
                "end_date": end_date.strip() or None,
                "description": None,
                "skills": [],
                "_bullets": [],
            }
            continue

        if current is None:
            continue

        if current["employer"] is None and not line.startswith("•"):
            employer_part, _, _rest = line.partition("|")
            current["employer"] = employer_part.strip() or line.strip()
            continue

        bullet_text = line.lstrip("•").strip()
        if bullet_text:
            current["_bullets"].append(bullet_text)

    if current:
        entries.append(current)

    for entry in entries:
        entry["description"] = " ".join(entry.pop("_bullets")) or None

        combined_text = f"{entry['description'] or ''}".lower()
        entry["skills"] = sorted(
            {
                skill
                for skill in known_skill_names
                if skill.lower() in combined_text
            }
        )

    return [e for e in entries if e["employer"]]


# ---------------------------------------------------------------------
# Certifications
# ---------------------------------------------------------------------

def _extract_certifications(certification_lines):
    """
    One bullet ("• ...") starts a new certification; a following
    line that does NOT start with a bullet is a wrapped continuation of
    the same certification's text (a single logical bullet line-wrapped
    by the PDF layout), appended rather than treated as a separate
    certification.
    """
    certifications = []

    for line in certification_lines:
        if line.startswith("•"):
            text = line.lstrip("•").strip()
            if text:
                certifications.append(text)
        elif certifications:
            certifications[-1] = f"{certifications[-1]} {line.strip()}"

    return [{"name": text} for text in certifications]


# ---------------------------------------------------------------------
# Education
# ---------------------------------------------------------------------

_YEAR_PATTERN = re.compile(r"\b(19|20)\d{2}\b")


def _extract_education(education_lines):
    education = []

    for i in range(0, len(education_lines) - 1, 2):
        degree_line = education_lines[i]
        institution_line = education_lines[i + 1]

        degree_parts = re.split(r"\s+[–\-]\s+", degree_line, maxsplit=1)
        degree = degree_parts[0]
        field_of_study = degree_parts[1] if len(degree_parts) > 1 else None

        institution_part, _, year_part = institution_line.partition("|")
        institution = institution_part.strip() or institution_line.strip()

        year_match = _YEAR_PATTERN.search(year_part or institution_line)
        end_date = year_match.group(0) if year_match else None

        education.append(
            {
                "institution": institution,
                "degree": degree.strip() if degree else None,
                "field_of_study": field_of_study.strip() if field_of_study else None,
                "end_date": end_date,
            }
        )

    return education


# ---------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------

def parse_resume_text(text, candidate_id):
    """
    Parse already-extracted resume text into a raw dict matching
    candidate_profile.py's canonical schema, with profile_status=DRAFT
    and metadata.source=RESUME_EXTRACTED. Pure text processing -- no
    PDF dependency, so this is directly unit-testable against
    hand-written fixture text.
    """
    if not candidate_id or not isinstance(candidate_id, str):
        raise ValueError("candidate_id is required and must be a non-empty string")

    sections = _split_into_sections(text)

    identity = _extract_identity(sections.get("_preamble", []))
    professional_summary = _extract_professional_summary(sections.get("summary", []))
    skills = _extract_skills(sections.get("skills", []))

    known_skill_names = {
        skill["name"]
        for skill_list in skills.values()
        for skill in skill_list
    }

    employment_history = _extract_employment_history(
        sections.get("experience", []), known_skill_names
    )
    certifications = _extract_certifications(sections.get("certifications", []))
    education = _extract_education(sections.get("education", []))

    current_title = next(
        (e["title"] for e in employment_history if e["end_date"] == "Present"),
        None,
    )

    return {
        "identity": {"candidate_id": candidate_id, **identity},
        "professional_summary": {
            **professional_summary,
            "current_title": current_title,
        },
        "skills": skills,
        "certifications": certifications,
        "education": education,
        "employment_history": employment_history,
        "job_preferences": {},
        "metadata": {
            "profile_status": "DRAFT",
            "confirmed_by_user": False,
            "source": Provenance.RESUME_EXTRACTED.value,
        },
    }


def extract_candidate_profile_draft(pdf_path, candidate_id):
    """
    Extract a resume PDF into a DRAFT CandidateProfile with
    metadata.source=RESUME_EXTRACTED. Never promotes to CONFIRMED --
    that requires a separate, explicit user review/confirm step (see
    candidate_profile.promote_to_confirmed()).
    """
    text = extract_text_from_pdf(pdf_path)
    raw = parse_resume_text(text, candidate_id)
    return normalize_candidate_profile(raw)


@dataclass
class ExtractionResult:
    profile: object
    source_path: str
    status: str  # "SUCCESS" or "FAILED"
    validation_result: object = None
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def extract_candidate_profile_draft_result(pdf_path, candidate_id):
    """
    Same extraction as extract_candidate_profile_draft() (unmodified,
    called as-is below), reported through an explicit ExtractionResult
    instead of a bare CandidateProfile / raised exception.

    status="SUCCESS" means extraction produced a CandidateProfile
    object without raising -- it does NOT mean the profile is free of
    validation issues (see `warnings`/`errors`, sourced from
    candidate_profile.validate_candidate_profile()). A resume with, say,
    an unrecognized target location would still be status="SUCCESS"
    with a WARNING in `warnings`; a resume that fails to parse into a
    structurally valid profile at all (e.g. an unreadable PDF) is
    status="FAILED", with `profile=None` and the exception message in
    `errors`.

    Never changes profile_status or provenance -- those are exactly
    what extract_candidate_profile_draft() itself produces
    (DRAFT / RESUME_EXTRACTED).
    """
    source_path = str(pdf_path)

    try:
        profile = extract_candidate_profile_draft(pdf_path, candidate_id)
    except Exception as error:
        return ExtractionResult(
            profile=None,
            source_path=source_path,
            status="FAILED",
            validation_result=None,
            warnings=[],
            errors=[str(error)],
        )

    validation_result = validate_candidate_profile(profile)

    return ExtractionResult(
        profile=profile,
        source_path=source_path,
        status="SUCCESS",
        validation_result=validation_result,
        warnings=[issue.message for issue in validation_result.warning_issues],
        errors=[issue.message for issue in validation_result.error_issues],
    )
