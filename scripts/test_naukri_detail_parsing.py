#!/usr/bin/env python3

import sys
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from source_adapter import RawJob
from scripts.naukri_parser import parse_detail_page

FIXTURES_DIR = ROOT / "data" / "fixtures" / "naukri"

EXPECTED_JOB_URL = (
    "https://www.naukri.com/job-listings-site-reliability-engineer-"
    "tata-consultancy-services-bengaluru-8-to-10-years-190626026419"
)

EXPECTED_TITLE = "Site Reliability Engineer"
EXPECTED_COMPANY = "Tata Consultancy Services"
EXPECTED_LOCATION_SUBSTRING = "Bengaluru"
EXPECTED_EXPERIENCE_SUBSTRING = "8 - 10 years"
EXPECTED_POSTED_SUBSTRING = "Few hours ago"

# Verified present verbatim in data/fixtures/naukri/detail_valid.html,
# inside the real "Job Description" section.
EXPECTED_JD_SUBSTRING = (
    "Develop, test, and maintain high-quality software solutions"
)

# Verified present verbatim in the real "Key Skills" section of
# data/fixtures/naukri/detail_valid.html -- the six skill chips
# between the "Key Skills" heading and the "About the company"
# section (confirmed by direct inspection of the raw HTML markup,
# each wrapped in <a class="styles_chip__..."><span>...</span></a>).
EXPECTED_SKILLS = sorted([
    "DevOps",
    "Cloud Services",
    "Sre",
    "Site Reliability Engineering",
    "Python",
    "Ci/Cd",
])


def load_fixture(filename):
    return (FIXTURES_DIR / filename).read_text(encoding="utf-8")


def main():
    failures = []

    print("NAUKRI DETAIL PAGE PARSING TEST")
    print("================================")

    html = load_fixture("detail_valid.html")
    result = parse_detail_page(html, EXPECTED_JOB_URL)

    # --- exact title ---
    if result.get("title") != EXPECTED_TITLE:
        failures.append(
            f"title: expected {EXPECTED_TITLE!r}, got {result.get('title')!r}"
        )
    else:
        print(f"PASS: title -> {result['title']!r}")

    # --- exact company ---
    if result.get("company") != EXPECTED_COMPANY:
        failures.append(
            f"company: expected {EXPECTED_COMPANY!r}, got {result.get('company')!r}"
        )
    else:
        print(f"PASS: company -> {result['company']!r}")

    # --- location contains Bengaluru ---
    location = result.get("location", "")
    if EXPECTED_LOCATION_SUBSTRING not in location:
        failures.append(
            "location: expected to contain "
            f"{EXPECTED_LOCATION_SUBSTRING!r}, got {location!r}"
        )
    else:
        print(f"PASS: location -> {location!r}")

    # --- experience_required contains 8 - 10 years ---
    experience = result.get("experience_required", "")
    if EXPECTED_EXPERIENCE_SUBSTRING not in experience:
        failures.append(
            "experience_required: expected to contain "
            f"{EXPECTED_EXPERIENCE_SUBSTRING!r}, got {experience!r}"
        )
    else:
        print(f"PASS: experience_required -> {experience!r}")

    # --- posted_date contains Few hours ago ---
    posted_date = result.get("posted_date", "")
    if EXPECTED_POSTED_SUBSTRING not in posted_date:
        failures.append(
            "posted_date: expected to contain "
            f"{EXPECTED_POSTED_SUBSTRING!r}, got {posted_date!r}"
        )
    else:
        print(f"PASS: posted_date -> {posted_date!r}")

    # --- jd_text non-empty and contains real Job Description content ---
    jd_text = result.get("jd_text", "")
    if not jd_text:
        failures.append("jd_text: expected non-empty, got empty string")
    elif EXPECTED_JD_SUBSTRING not in jd_text:
        failures.append(
            f"jd_text: expected to contain {EXPECTED_JD_SUBSTRING!r}, "
            f"got {jd_text[:200]!r}..."
        )
    else:
        print("PASS: jd_text is non-empty and contains real JD content")

    # --- job_url is the confirmed TCS URL ---
    if result.get("job_url") != EXPECTED_JOB_URL:
        failures.append(
            f"job_url: expected {EXPECTED_JOB_URL!r}, got {result.get('job_url')!r}"
        )
    else:
        print(f"PASS: job_url -> {result['job_url']}")

    # --- mandatory_skills and preferred_skills must both be lists ---
    mandatory_skills = result.get("mandatory_skills")
    preferred_skills = result.get("preferred_skills")

    if not isinstance(mandatory_skills, list):
        failures.append(
            "mandatory_skills: expected a list, got "
            f"{type(mandatory_skills).__name__}"
        )
    else:
        print(f"PASS: mandatory_skills is a list ({mandatory_skills!r})")

    if not isinstance(preferred_skills, list):
        failures.append(
            "preferred_skills: expected a list, got "
            f"{type(preferred_skills).__name__}"
        )
    else:
        print("PASS: preferred_skills is a list")

        # --- skills must match the real Key Skills section exactly ---
        if sorted(preferred_skills) != EXPECTED_SKILLS:
            failures.append(
                f"preferred_skills: expected {EXPECTED_SKILLS!r}, "
                f"got {sorted(preferred_skills)!r}"
            )
        else:
            print(
                "PASS: preferred_skills matches the real "
                "Key Skills section exactly"
            )

        # --- skills must not leak "About the company" boilerplate ---
        leaked = [
            skill for skill in preferred_skills
            if "about the company" in skill.lower()
        ]

        if leaked:
            failures.append(
                f"preferred_skills: contains leaked non-skill content: {leaked!r}"
            )
        else:
            print(
                "PASS: preferred_skills does not contain "
                "'About the company' text"
            )

    # --- returned object must be RawJob-shape compatible ---
    raw_job_field_names = {f.name for f in fields(RawJob)}
    missing_fields = raw_job_field_names - set(result.keys())

    if missing_fields:
        failures.append(
            f"result is missing RawJob-contract fields: {sorted(missing_fields)}"
        )
    else:
        print("PASS: result contains all RawJob-contract fields")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(failure)
        sys.exit(1)

    print("\nAll Naukri detail-page parsing tests passed.")


if __name__ == "__main__":
    main()
