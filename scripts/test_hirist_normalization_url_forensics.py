#!/usr/bin/env python3

"""
Characterization tests for the Phase 6 Step 8 forensic findings
(data/reports/hirist_normalization_url_forensics.md). hirist_parser.py
was unmodified by Step 8 itself; ONE of the four gaps documented here
(A, the top-level url field) was subsequently fixed by Phase 6 Step 10
(data/reports/hirist_job_url_remediation.md), using real evidence from
Step 9's live capture -- test A below was updated at that time to
assert the new, correct behavior instead of the old gap. B, C, and D
remain genuinely unfixed and are still characterization-only.

  A. [UPDATED by Step 10] The flat "name" extraction path now reads a
     top-level "url" field on the ListItem itself (Step 9 confirmed
     this is the shape every real entry actually uses). "@id" and
     "sameAs" are still never read -- neither was ever observed in any
     real entry.
  B. "application_url" is never populated by ANY code path (flat or
     nested) -- it is only ever set to "" in _empty_raw_job() and never
     assigned anywhere else in the module. [unchanged by Step 10]
  C. "baseSalary" and "experienceRequirements" are never read by any
     code path either, despite the module docstring listing baseSalary
     as part of the schema.org vocabulary "applied defensively" --
     that vocabulary is not actually implemented in _parse_job_entry().
     [unchanged by Step 10]
  D. The " - " flat-name delimiter split reproduces the exact wrong
     result Step 7 observed live ("Senior DevOps Engineer - AWS &
     Kubernetes" splits to company="Senior DevOps Engineer",
     title="AWS & Kubernetes") -- confirming this is a genuine,
     reproducible parser limitation, not a one-off artifact of the
     live run. [unchanged by Step 10 -- company/title parsing is
     explicitly out of scope for that remediation]

Makes ZERO network requests -- pure, offline, in-memory dicts only.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from hirist_parser import _parse_job_entry


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def test_A_flat_entry_reads_top_level_url_not_id_or_sameas(failures):
    # UPDATED by Phase 6 Step 10 (data/reports/hirist_job_url_remediation.md):
    # Step 9's real, untruncated live capture confirmed every real
    # ListItem entry has a top-level "url" field, and Step 10
    # implemented reading it -- this is the ONE evidence-backed field
    # this task's own Step 8 forensics identified as fixable. "@id"
    # and "sameAs" were NEVER observed in any real entry (Step 9
    # confirmed their absence too) and are still NOT read -- inventing
    # support for fields that were never observed would violate this
    # project's strict-evidence discipline. This test originally
    # documented job_url as an unfixed gap; it now documents the
    # post-Step-10 behavior instead.
    entry = {
        "@type": "ListItem",
        "position": 1,
        "name": "Acme Corp - Staff Engineer",
        "url": "https://www.hirist.tech/job-detail/staff-engineer-999",
        "@id": "https://www.hirist.tech/job-detail/staff-engineer-id-should-be-ignored",
        "sameAs": "https://www.hirist.tech/job-detail/staff-engineer-sameas-should-be-ignored",
    }
    job = _parse_job_entry(entry)

    if job is None:
        return _fail(failures, "A: expected the flat entry to parse successfully (valid single delimiter), got None")
    if job["job_url"] != "https://www.hirist.tech/job-detail/staff-engineer-999":
        return _fail(
            failures,
            f"A: expected job_url to equal the top-level 'url' field (Step 10 remediation), got {job['job_url']!r}",
        )

    print("PASS: A -> flat-name entries now correctly read a top-level 'url' field (Step 10 remediation); '@id'/'sameAs' remain unread, since neither was ever observed in real data")


def test_B_application_url_never_populated(failures):
    flat_entry = {"@type": "ListItem", "position": 1, "name": "Acme Corp - Staff Engineer"}
    flat_job = _parse_job_entry(flat_entry)
    if flat_job is None or flat_job["application_url"] != "":
        return _fail(failures, f"B: expected application_url == '' for flat entry, got {flat_job}")

    nested_entry = {
        "@type": "ListItem",
        "position": 1,
        "item": {
            "title": "Staff Engineer",
            "hiringOrganization": {"name": "Acme Corp"},
            "url": "https://www.hirist.tech/job-detail/staff-engineer-999",
        },
    }
    nested_job = _parse_job_entry(nested_entry)
    if nested_job is None or nested_job["application_url"] != "":
        return _fail(failures, f"B: expected application_url == '' for nested entry too, got {nested_job}")

    print("PASS: B -> application_url is never populated by either extraction path (documented code gap)")


def test_C_salary_and_experience_never_extracted(failures):
    nested_entry = {
        "@type": "ListItem",
        "position": 1,
        "item": {
            "title": "Staff Engineer",
            "hiringOrganization": {"name": "Acme Corp"},
            "baseSalary": {"@type": "MonetaryAmount", "currency": "INR", "value": {"value": 3000000}},
            "experienceRequirements": "5-8 years",
        },
    }
    job = _parse_job_entry(nested_entry)
    if job is None:
        return _fail(failures, "C: expected the nested entry to parse successfully, got None")
    if job["experience_required"] != "":
        return _fail(
            failures,
            f"C: expected experience_required == '' (experienceRequirements is never read by any code "
            f"path), got {job['experience_required']!r}",
        )
    if "salary" in job and job.get("salary"):
        return _fail(failures, f"C: unexpectedly found a populated salary-like field: {job}")

    print("PASS: C -> baseSalary and experienceRequirements are never extracted by any code path, despite baseSalary being named in the module docstring's vocabulary list (docstring/code mismatch)")


def test_D_delimiter_split_reproduces_step7_wrong_result(failures):
    entry = {"@type": "ListItem", "position": 1, "name": "Senior DevOps Engineer - AWS & Kubernetes"}
    job = _parse_job_entry(entry)

    if job is None:
        return _fail(failures, "D: expected this entry to parse (exactly one ' - ' occurrence), got None")
    if job["company"] != "Senior DevOps Engineer" or job["title"] != "AWS & Kubernetes":
        return _fail(
            failures,
            f"D: expected the Step 7 wrong-split result to reproduce exactly "
            f"(company='Senior DevOps Engineer', title='AWS & Kubernetes'), got "
            f"company={job['company']!r}, title={job['title']!r}",
        )

    print("PASS: D -> the ' - ' delimiter split reproduces the exact wrong company/title split Step 7 observed live, confirming it is a genuine, reproducible limitation")


def main():
    print("HIRIST NORMALIZATION / JOB-URL FORENSICS -- CHARACTERIZATION TESTS (Phase 6 Step 8)")
    print("=========================================================================")

    failures = []

    test_A_flat_entry_reads_top_level_url_not_id_or_sameas(failures)
    test_B_application_url_never_populated(failures)
    test_C_salary_and_experience_never_extracted(failures)
    test_D_delimiter_split_reproduces_step7_wrong_result(failures)

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll Step 8 characterization tests passed (documenting existing limitations, not fixing them).")


if __name__ == "__main__":
    main()
