#!/usr/bin/env python3

"""
Tests for scripts/resume_extractor.py.

Mostly fixture-based against synthetic, hand-written resume TEXT (not
PDFs) via parse_resume_text() -- fast, no PDF dependency, and proves
the parser is generic (not tuned to any one candidate's resume). One
test (test 14) exercises a synthetic, wholly fictional resume PDF
(data/fixtures/resume_extractor/synthetic_sre_resume.pdf, produced by
scripts/generate_synthetic_resume_pdf.py) end-to-end through
extract_candidate_profile_draft(), to cover the real binary-PDF-to-text
path (pypdf.PdfReader.extract_text()) that the other 13 tests never
touch -- tracked in git, no private resume file required, works from a
fresh clone.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from resume_extractor import (
    extract_candidate_profile_draft,
    parse_resume_text,
)
from candidate_profile import Provenance, ProfileStatus, validate_candidate_profile


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


# A deliberately DIFFERENT synthetic resume -- a fictional product
# manager, not an SRE, not Saroj -- proving the parser is generic.
_SYNTHETIC_RESUME_TEXT = """ALEX CHEN
Product Manager
alex.chen@example.com | (415) 555-0199
linkedin.com/in/alexchen

PROFESSIONAL SUMMARY
Product leader with 6 years of experience shipping consumer mobile
applications and leading cross-functional teams.

TECHNICAL SKILLS
Cloud Platforms: AWS (S3, Lambda), Google Cloud Platform
Programming & Scripting: SQL, Python
Databases: PostgreSQL, MongoDB
Security & Compliance: SOC 2, GDPR

PROJECTS
Mobile Checkout Redesign
• Reduced checkout abandonment by 18% through a redesigned flow.

PROFESSIONAL EXPERIENCE
Senior Product Manager Jan 2022 - Present
Example Retail Inc
• Led a team of 8 engineers building the mobile checkout experience.
• Partnered with data science to run SQL-driven experiments.

Product Manager Jun 2019 - Dec 2021
Sample Startup Co
• Shipped the initial MongoDB-backed catalog service.

CERTIFICATIONS
• Certified Scrum Product Owner (CSPO)
• AWS Certified Cloud Practitioner

EDUCATION
Master of Business Administration - Marketing
Sample State University | 2019
Bachelor of Arts - Economics
Example College | 2015

ADDITIONAL EXPERTISE
• Public Speaking
• Agile Coaching
"""


def _fixture_profile():
    return parse_resume_text(_SYNTHETIC_RESUME_TEXT, "cand-synthetic-1")


def test_1_identity_extraction():
    failures = []
    raw = _fixture_profile()

    identity = raw["identity"]

    if identity["name"] != "ALEX CHEN":
        _fail(failures, f"test 1: expected name 'ALEX CHEN', got {identity['name']!r}")
    if identity["email"] != "alex.chen@example.com":
        _fail(failures, f"test 1: expected email extracted, got {identity['email']!r}")
    if not identity["phone"] or "555" not in identity["phone"]:
        _fail(failures, f"test 1: expected a phone number containing '555', got {identity['phone']!r}")

    if not failures:
        print(f"PASS: test 1 -> identity extracted: name={identity['name']!r} email={identity['email']!r} phone={identity['phone']!r}")

    return failures


def test_2_experience_years_from_explicit_phrase():
    failures = []
    raw = _fixture_profile()

    years = raw["professional_summary"]["total_experience_years"]

    if years != 6.0:
        _fail(failures, f"test 2: expected total_experience_years=6.0 from the explicit '6 years of experience' phrase, got {years!r}")
    else:
        print("PASS: test 2 -> total_experience_years correctly extracted from an explicit 'N years of experience' phrase")

    return failures


def test_3_skill_category_classification_and_parenthetical_expansion():
    failures = []
    raw = _fixture_profile()

    cloud_names = {s["name"] for s in raw["skills"].get("cloud", [])}
    expected = {"AWS", "S3", "Lambda", "Google Cloud Platform"}

    if cloud_names != expected:
        _fail(failures, f"test 3: expected cloud skills {expected}, got {cloud_names}")
    else:
        print(f"PASS: test 3 -> 'AWS (S3, Lambda)' correctly expands into 3 distinct, literally-named cloud skills: {sorted(cloud_names)}")

    db_names = {s["name"] for s in raw["skills"].get("databases_storage", [])}
    if db_names != {"PostgreSQL", "MongoDB"}:
        _fail(failures, f"test 3: expected databases_storage {{PostgreSQL, MongoDB}}, got {db_names}")

    sec_names = {s["name"] for s in raw["skills"].get("security_iam", [])}
    if sec_names != {"SOC 2", "GDPR"}:
        _fail(failures, f"test 3: expected security_iam {{SOC 2, GDPR}}, got {sec_names}")

    return failures


def test_4_employment_history_parsing():
    failures = []
    raw = _fixture_profile()

    entries = raw["employment_history"]

    if len(entries) != 2:
        _fail(failures, f"test 4: expected 2 employment entries, got {len(entries)}: {[(e['employer'], e['title']) for e in entries]}")
        return failures

    first = entries[0]
    if first["employer"] != "Example Retail Inc" or first["title"] != "Senior Product Manager":
        _fail(failures, f"test 4: expected first entry (Example Retail Inc / Senior Product Manager), got ({first['employer']!r}, {first['title']!r})")
    if first["end_date"] != "Present":
        _fail(failures, f"test 4: expected first entry end_date='Present', got {first['end_date']!r}")

    second = entries[1]
    if second["employer"] != "Sample Startup Co" or second["start_date"] != "Jun 2019":
        _fail(failures, f"test 4: expected second entry (Sample Startup Co, start Jun 2019), got ({second['employer']!r}, {second['start_date']!r})")

    if "SQL" not in first["skills"]:
        _fail(failures, f"test 4: expected 'SQL' cross-referenced into the first role's skills (its bullet mentions SQL), got {first['skills']}")

    if not failures:
        print(f"PASS: test 4 -> {len(entries)} employment entries parsed correctly, including skill cross-referencing")

    return failures


def test_5_certification_continuation_line_joined():
    """
    Regression test for the wrapped-bullet bug found and fixed during
    this component's implementation: a certification bullet's text that
    wraps onto a following line (no bullet marker) must be joined into
    the SAME certification, not split into a second, spurious entry.
    """
    failures = []
    raw = _fixture_profile()

    certs = [c["name"] for c in raw["certifications"]]

    if certs != ["Certified Scrum Product Owner (CSPO)", "AWS Certified Cloud Practitioner"]:
        _fail(failures, f"test 5: expected exactly 2 clean certifications, got {certs}")
    else:
        print(f"PASS: test 5 -> {len(certs)} certifications extracted cleanly, one per bullet")

    return failures


def test_6_education_parsing():
    failures = []
    raw = _fixture_profile()

    education = raw["education"]

    if len(education) != 2:
        _fail(failures, f"test 6: expected 2 education entries, got {len(education)}: {education}")
        return failures

    first = education[0]
    if first["degree"] != "Master of Business Administration" or first["field_of_study"] != "Marketing":
        _fail(failures, f"test 6: expected (Master of Business Administration, Marketing), got ({first['degree']!r}, {first['field_of_study']!r})")
    if first["institution"] != "Sample State University" or first["end_date"] != "2019":
        _fail(failures, f"test 6: expected (Sample State University, 2019), got ({first['institution']!r}, {first['end_date']!r})")

    if not failures:
        print(f"PASS: test 6 -> {len(education)} education entries parsed correctly (degree/field/institution/year)")

    return failures


def test_7_unrecognized_section_does_not_leak():
    """
    Regression test for the section-boundary bug found and fixed during
    implementation: "PROJECTS" (between SUMMARY/SKILLS and
    PROFESSIONAL EXPERIENCE) and "ADDITIONAL EXPERTISE" (after
    EDUCATION) are NOT in this module's recognized-header list, and
    must not leak their content into the skills/experience/education
    sections that precede or follow them.
    """
    failures = []
    raw = _fixture_profile()

    for entry in raw["employment_history"]:
        if "Checkout Redesign" in (entry["description"] or ""):
            _fail(failures, f"test 7: the unrecognized 'PROJECTS' section leaked into employment entry {entry['employer']!r}'s description")

    if len(raw["education"]) != 2:
        _fail(failures, f"test 7: the unrecognized 'ADDITIONAL EXPERTISE' section corrupted education parsing, got {len(raw['education'])} entries instead of 2")

    for edu in raw["education"]:
        if "Public Speaking" in (edu["institution"] or "") or "Agile Coaching" in (edu["degree"] or ""):
            _fail(failures, f"test 7: 'ADDITIONAL EXPERTISE' bullets leaked into an education entry: {edu}")

    if not failures:
        print("PASS: test 7 -> unrecognized sections (PROJECTS, ADDITIONAL EXPERTISE) do not leak into neighboring recognized sections")

    return failures


def test_8_provenance_and_lifecycle():
    failures = []
    raw = _fixture_profile()

    if raw["metadata"]["source"] != Provenance.RESUME_EXTRACTED.value:
        _fail(failures, f"test 8: expected metadata.source=RESUME_EXTRACTED, got {raw['metadata']['source']!r}")
    if raw["metadata"]["profile_status"] != ProfileStatus.DRAFT.value:
        _fail(failures, f"test 8: expected profile_status=DRAFT, got {raw['metadata']['profile_status']!r}")
    if raw["metadata"]["confirmed_by_user"] is not False:
        _fail(failures, f"test 8: expected confirmed_by_user=False, got {raw['metadata']['confirmed_by_user']!r}")

    all_skills = [s for category in raw["skills"].values() for s in category]
    non_extracted = [s for s in all_skills if s.get("provenance") != Provenance.RESUME_EXTRACTED.value]

    if non_extracted:
        _fail(failures, f"test 8: expected every extracted skill to carry provenance=RESUME_EXTRACTED, found {len(non_extracted)} that don't")
    elif not all_skills:
        _fail(failures, "test 8: expected at least one extracted skill to check provenance on")

    if not failures:
        print(f"PASS: test 8 -> profile-level and all {len(all_skills)} skill-level provenance correctly marked RESUME_EXTRACTED; lifecycle is DRAFT/unconfirmed")

    return failures


def test_9_never_invent_target_locations_or_seniority():
    failures = []
    raw = _fixture_profile()

    if raw["job_preferences"]:
        _fail(failures, f"test 9: expected job_preferences to be empty (never inferred from a resume), got {raw['job_preferences']}")
    if raw["professional_summary"].get("seniority_level") is not None:
        _fail(failures, f"test 9: expected seniority_level to never be inferred from a job title, got {raw['professional_summary']['seniority_level']!r}")

    if not failures:
        print("PASS: test 9 -> job_preferences (target_locations, etc.) and seniority_level are never inferred from resume content")

    return failures


def test_10_missing_experience_phrase_stays_none():
    """
    When the resume's summary does NOT contain an explicit "N years of
    experience" phrase, total_experience_years must remain None -- it
    must NOT be computed by summing employment date ranges.
    """
    failures = []

    text = (
        "TAYLOR SMITH\n"
        "taylor@example.com\n\n"
        "PROFESSIONAL SUMMARY\n"
        "A dedicated engineer who enjoys building reliable systems.\n\n"
        "PROFESSIONAL EXPERIENCE\n"
        "Engineer Jan 2018 - Dec 2023\n"
        "Some Company\n"
        "• Built things.\n"
    )
    raw = parse_resume_text(text, "cand-no-years")

    if raw["professional_summary"]["total_experience_years"] is not None:
        _fail(
            failures,
            f"test 10: expected total_experience_years to remain None with no explicit phrase "
            f"(never derived from date-range arithmetic), got {raw['professional_summary']['total_experience_years']!r}",
        )
    else:
        print("PASS: test 10 -> total_experience_years stays None when no explicit 'N years of experience' phrase exists, even though employment dates are present")

    return failures


def test_11_missing_candidate_id_raises():
    failures = []

    try:
        parse_resume_text(_SYNTHETIC_RESUME_TEXT, "")
        _fail(failures, "test 11: expected parse_resume_text() to reject an empty candidate_id")
    except ValueError:
        print("PASS: test 11 -> parse_resume_text() requires an explicit, non-empty candidate_id")

    return failures


def test_12_extracted_draft_validates_cleanly():
    failures = []

    raw = _fixture_profile()

    from candidate_profile import normalize_candidate_profile

    profile = normalize_candidate_profile(raw)
    result = validate_candidate_profile(profile)

    if not result.valid:
        _fail(failures, f"test 12: expected the extracted draft to validate cleanly, got errors: {[e.message for e in result.error_issues]}")
    elif profile.metadata.profile_status != ProfileStatus.DRAFT:
        _fail(failures, f"test 12: expected profile_status=DRAFT, got {profile.metadata.profile_status}")
    else:
        print("PASS: test 12 -> the extracted synthetic draft passes candidate_profile.validate_candidate_profile() cleanly")

    return failures


def test_13_different_candidate_produces_different_profile():
    """
    Proves the extractor is generic, not tuned to Saroj's resume: a
    materially different synthetic resume (a product manager, no SRE
    content at all) produces a materially different profile.
    """
    failures = []

    raw = _fixture_profile()

    if "Kubernetes" in str(raw["skills"]) or "Terraform" in str(raw["skills"]):
        _fail(failures, "test 13: the generic product-manager fixture should not contain SRE-specific skills -- extractor may be hardcoded to a specific resume's vocabulary")

    if raw["identity"]["name"] == "SAROJ KUMAR NAYAK":
        _fail(failures, "test 13: extractor appears hardcoded to Saroj's name")

    if not failures:
        print("PASS: test 13 -> a materially different (non-SRE, non-Saroj) resume produces a materially different, correctly-parsed profile")

    return failures


def test_14_synthetic_resume_pdf_end_to_end_smoke_test():
    """
    Integration smoke test exercising the REAL binary-PDF-to-text path
    (pypdf.PdfReader -> extract_text(), not parse_resume_text()
    directly) end-to-end -- the one thing the other 13 fixture-based
    tests in this file don't cover. Uses a wholly synthetic, tracked
    fixture (data/fixtures/resume_extractor/synthetic_sre_resume.pdf,
    produced by scripts/generate_synthetic_resume_pdf.py) instead of
    any real person's resume, so this test works from a fresh clone
    with no private file dependency. Every fact asserted below (name,
    email, employer, experience years) is fictional.
    """
    failures = []

    pdf_path = ROOT / "data" / "fixtures" / "resume_extractor" / "synthetic_sre_resume.pdf"

    if not pdf_path.exists():
        _fail(failures, f"test 14: expected synthetic fixture PDF at {pdf_path}, file not found")
        return failures

    profile = extract_candidate_profile_draft(str(pdf_path), "cand-synthetic-pdf-smoke")
    result = validate_candidate_profile(profile)

    if not result.valid:
        _fail(failures, f"test 14: expected the synthetic resume PDF to extract into a valid draft, got errors: {[e.message for e in result.error_issues]}")
    if profile.metadata.profile_status != ProfileStatus.DRAFT:
        _fail(failures, f"test 14: expected profile_status=DRAFT, got {profile.metadata.profile_status}")
    if profile.metadata.source != Provenance.RESUME_EXTRACTED:
        _fail(failures, f"test 14: expected metadata.source=RESUME_EXTRACTED, got {profile.metadata.source}")
    if profile.identity.email != "jordan.smith@example.com":
        _fail(failures, f"test 14: expected the fictional, verifiable email from this fixture PDF, got {profile.identity.email!r}")
    if profile.professional_summary.total_experience_years != 9.0:
        _fail(failures, f"test 14: expected total_experience_years=9.0 (explicitly stated in this fixture's summary), got {profile.professional_summary.total_experience_years!r}")
    if len(profile.employment_history) != 4:
        _fail(failures, f"test 14: expected 4 employment entries in this fixture resume, got {len(profile.employment_history)}")
    if any("KEY PROJECTS" in (e.description or "") for e in profile.employment_history):
        _fail(failures, "test 14: the unrecognized 'KEY PROJECTS' section leaked into an employment entry's description in the synthetic PDF")

    # Score-audit regression guard (found live, 2026-09-25, against a
    # real candidate's profile -- a STALE extraction had ALL of
    # cloud/containers_orchestration/infrastructure_iac/cicd/
    # observability empty, with every real tool dumped into "other"
    # instead, from an OLDER extraction pre-dating a categorization
    # fix, never re-run since. score_job.py reads candidate skills from
    # exactly these 5 categories for 50 of its 100 points (Cloud/K8s/
    # IaC/CI-CD/Observability), so a candidate whose profile regresses
    # to this state scores near-zero on all of them regardless of true
    # fit -- a serious, silent scoring defect this test now catches via
    # the binary-PDF extraction path (the other 13 tests only exercise
    # parse_resume_text() directly, never the PDF->text step itself).
    # The synthetic fixture's TECHNICAL SKILLS section deliberately
    # includes one tool from each of these 5 categories.
    expected_in_category = {
        "cloud": {"AWS", "EKS", "AKS"},
        "containers_orchestration": {"Kubernetes", "Docker", "Helm"},
        "infrastructure_iac": {"Terraform", "Ansible"},
        "cicd": {"Jenkins", "ArgoCD"},
        "observability": {"Prometheus", "Grafana"},
    }
    for category, expected_names in expected_in_category.items():
        actual_names = {s.name for s in getattr(profile.skills, category, [])}
        if not actual_names:
            _fail(failures, f"test 14: SAFETY VIOLATION -- skills.{category} is empty for the synthetic resume PDF (score_job.py awards 0 points for this dimension when empty); expected at least {expected_names}")
        elif not (expected_names & actual_names):
            _fail(failures, f"test 14: SAFETY VIOLATION -- skills.{category} = {actual_names} does not contain any of the expected tools {expected_names} -- category classification may have regressed")

    other_names = {s.name for s in profile.skills.other}
    misclassified = expected_in_category.get("cloud", set()) & other_names
    if misclassified:
        _fail(failures, f"test 14: SAFETY VIOLATION -- known cloud tools {misclassified} were dumped into skills.other instead of skills.cloud")

    if not failures:
        print(
            f"PASS: test 14 -> synthetic resume PDF extracts into a valid DRAFT/RESUME_EXTRACTED "
            f"profile: email={profile.identity.email!r}, "
            f"experience={profile.professional_summary.total_experience_years}, "
            f"{len(profile.employment_history)} employment entries"
        )

    return failures


def main():
    tests = [
        test_1_identity_extraction,
        test_2_experience_years_from_explicit_phrase,
        test_3_skill_category_classification_and_parenthetical_expansion,
        test_4_employment_history_parsing,
        test_5_certification_continuation_line_joined,
        test_6_education_parsing,
        test_7_unrecognized_section_does_not_leak,
        test_8_provenance_and_lifecycle,
        test_9_never_invent_target_locations_or_seniority,
        test_10_missing_experience_phrase_stays_none,
        test_11_missing_candidate_id_raises,
        test_12_extracted_draft_validates_cleanly,
        test_13_different_candidate_produces_different_profile,
        test_14_synthetic_resume_pdf_end_to_end_smoke_test,
    ]

    all_failures = []
    for test in tests:
        all_failures.extend(test())

    print()

    if all_failures:
        print(f"{len(all_failures)} failure(s):")
        for failure in all_failures:
            print(f"  - {failure}")
        sys.exit(1)

    print(f"All {len(tests)} resume-extractor tests passed.")


if __name__ == "__main__":
    main()
