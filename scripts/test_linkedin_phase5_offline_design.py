#!/usr/bin/env python3

"""
Regression/grounding test for the Phase 5 Step 2 offline LinkedIn
integration design (data/reports/linkedin_phase5_offline_design.md).

Two purposes:
  1. Re-confirm LinkedIn's adapter status is untouched by this design
     step (NOT_ENABLED, empty capabilities) and that
     search_profile._default_sources() remains ['NAUKRI'].
  2. Confirm the design document's Section 4 (Normalized Job Mapping)
     field names are not invented -- every field it claims exists in
     discover_local.normalize_job()'s actual output, RawJob's actual
     fields, and location_taxonomy.WorkModel's actual enum members.

Makes NO live network/browser request of any kind. Does not import or
exercise any live-fetching code path.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from source_adapter import AdapterStatus, RawJob
from linkedin_adapter import LinkedInAdapter
import search_profile
from discover_local import normalize_job
from location_taxonomy import WorkModel


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def test_linkedin_still_not_enabled(failures):
    if LinkedInAdapter.status != AdapterStatus.NOT_ENABLED:
        return _fail(failures, f"LinkedInAdapter.status changed: {LinkedInAdapter.status}")

    if LinkedInAdapter.capabilities != frozenset():
        return _fail(failures, f"LinkedInAdapter.capabilities changed: {LinkedInAdapter.capabilities}")

    print("PASS: LinkedInAdapter remains status=NOT_ENABLED, capabilities=frozenset()")


def test_default_sources_still_naukri_only(failures):
    """Phase 11 update: HIRIST is now also genuinely ENABLED (unrelated
    to LinkedIn, which this file is actually about -- LinkedIn remains
    NOT_ENABLED, confirmed by the test above), so _default_sources()
    now includes both."""
    # Subset + explicit exclusion, not exact equality: _default_sources()
    # also includes any search-provider-backed source (*_SEARCH) ENABLED
    # in THIS environment's .env, which is correct and environment-
    # dependent (see search_provider.py's root-cause .env-loading fix).
    # This test's actual concern -- proven directly -- is that the
    # direct-crawl "LINKEDIN" skeleton key specifically stays excluded.
    defaults = search_profile._default_sources()
    if not {"NAUKRI", "HIRIST", "IIMJOBS", "APNA"} <= set(defaults):
        return _fail(failures, f"_default_sources() = {defaults!r}, expected it to include at least {{'NAUKRI', 'HIRIST', 'IIMJOBS', 'APNA'}}")
    if "LINKEDIN" in defaults:
        return _fail(failures, f"_default_sources() = {defaults!r}, the direct-crawl 'LINKEDIN' skeleton (still NOT_ENABLED) must not appear")

    print(f"PASS: search_profile._default_sources() includes the 4 direct sources (got {sorted(defaults)}) -- the direct-crawl LinkedIn skeleton correctly still excluded")


def test_design_doc_normalized_job_fields_are_real(failures):
    """
    Section 4 of the design doc claims a future LinkedIn adapter would
    map into these exact normalize_job() output keys. Confirm every one
    of them is actually produced today by the real, unmodified
    normalize_job() -- i.e. the design document invents no field.
    """
    sample_raw_job = {
        "source": "TESTSOURCE",
        "company": "Example Co",
        "title": "Example Title",
        "location": "Bengaluru",
        "work_model": "Hybrid",
        "job_url": "https://example.com/job/1",
        "application_url": "https://example.com/apply/1",
        "posted_date": "2 days ago",
        "jd_text": "Example description",
        "experience_required": "2 - 5 years",
        "mandatory_skills": ["Python"],
        "preferred_skills": ["AWS"],
        "experience_min_months": 24,
    }

    normalized = normalize_job(sample_raw_job, 1)

    expected_fields_from_design_doc = {
        "source", "company", "title", "location", "work_model",
        "job_url", "application_url", "posted_date", "jd_text",
        "experience_required", "mandatory_skills", "preferred_skills",
        "experience_min_months", "job_id",
    }

    actual_fields = set(normalized.keys())
    missing = expected_fields_from_design_doc - actual_fields
    if missing:
        return _fail(
            failures,
            f"design doc claims these normalize_job() fields exist, but they are missing: {missing}",
        )

    print(f"PASS: all {len(expected_fields_from_design_doc)} normalize_job() fields cited in the design doc are real")


def test_design_doc_raw_job_required_fields_are_real(failures):
    """
    Section 4/7 claim 'company' and 'title' are the only two fields
    normalize_job() strictly requires. Confirm that is still true --
    a fixture with only those two present must normalize successfully.
    """
    minimal_job = {"source": "TESTSOURCE", "company": "Example Co", "title": "Example Title"}

    try:
        normalized = normalize_job(minimal_job, 1)
    except Exception as error:  # noqa: BLE001
        return _fail(failures, f"a job dict with only company+title failed to normalize: {error}")

    if normalized["location"] != "" or normalized["work_model"] != "":
        return _fail(failures, "missing optional fields did not default to empty string as the design doc claims")

    print("PASS: company+title are confirmed as the only two required normalize_job() fields; others default to empty string")


def test_design_doc_work_model_enum_values_are_real(failures):
    """
    Section 4 claims a future LinkedIn work_model mapping would target
    REMOTE/HYBRID/ONSITE/UNKNOWN. Confirm these are the real,
    current WorkModel enum members (no invented value).
    """
    expected = {"REMOTE", "HYBRID", "ONSITE", "UNKNOWN"}
    actual = {member.name for member in WorkModel}

    if actual != expected:
        return _fail(failures, f"WorkModel enum members changed or mismatched: {actual}")

    print("PASS: WorkModel enum members match what the design doc cites (REMOTE, HYBRID, ONSITE, UNKNOWN)")


def test_design_doc_rawjob_dataclass_fields_are_real(failures):
    """
    Section 1/4 describe RawJob's fields as the adapter output contract.
    Confirm RawJob's actual dataclass fields match what the design doc
    describes.
    """
    expected_fields = {
        "source", "company", "title", "location", "work_model",
        "job_url", "application_url", "posted_date", "jd_text",
        "experience_required", "mandatory_skills", "preferred_skills",
    }
    actual_fields = set(RawJob.__dataclass_fields__.keys())

    if actual_fields != expected_fields:
        return _fail(failures, f"RawJob dataclass fields mismatch: {actual_fields}")

    print("PASS: RawJob dataclass fields match what the design doc describes")


def main():
    print("LINKEDIN PHASE 5 STEP 2 OFFLINE DESIGN GROUNDING TEST")
    print("========================================================")

    failures = []

    test_linkedin_still_not_enabled(failures)
    test_default_sources_still_naukri_only(failures)
    test_design_doc_normalized_job_fields_are_real(failures)
    test_design_doc_raw_job_required_fields_are_real(failures)
    test_design_doc_work_model_enum_values_are_real(failures)
    test_design_doc_rawjob_dataclass_fields_are_real(failures)

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll LinkedIn Phase 5 Step 2 offline design grounding tests passed.")


if __name__ == "__main__":
    main()
