#!/usr/bin/env python3

"""
Regression/grounding test for the Phase 6 Step 2 offline Hirist
integration design (data/reports/hirist_phase6_offline_design.md).

Two purposes:
  1. (Phase 6-10) Confirmed Hirist's adapter status was untouched by
     this design step (NOT_ENABLED, empty capabilities) and that
     search_profile._default_sources() remained ['NAUKRI']. Phase 11
     superseded this: HiristAdapter passed the full live-validation
     gate and is now genuinely ENABLED (see
     data/reports/phase11_public_multisource_completion.md) -- this
     test now confirms THAT state instead, since the design doc's own
     schema-reference claims (purpose 2 below) remain independently
     true regardless of enablement status.
  2. Confirm the design document's schema references are not invented
     -- every field/enum member it cites actually exists in
     discover_local.normalize_job()'s real output, RawJob's real
     fields, and location_taxonomy.WorkModel's real enum members.

Makes NO live network/browser request of any kind.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from source_adapter import AdapterStatus, AdapterCapability, RawJob
from hirist_adapter import HiristAdapter
import search_profile
from discover_local import normalize_job
from location_taxonomy import WorkModel


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def test_hirist_still_not_enabled(failures):
    """Phase 11 update: HiristAdapter is now genuinely ENABLED (full
    live-validation gate passed) -- see this file's module docstring."""
    expected_caps = {AdapterCapability.SEARCH, AdapterCapability.DETAIL, AdapterCapability.PAGINATION}
    if HiristAdapter.status != AdapterStatus.ENABLED:
        return _fail(failures, f"HiristAdapter.status = {HiristAdapter.status}, expected ENABLED (Phase 11)")

    if HiristAdapter.capabilities != expected_caps:
        return _fail(failures, f"HiristAdapter.capabilities = {HiristAdapter.capabilities}, expected {expected_caps}")

    print("PASS: HiristAdapter is status=ENABLED (Phase 11), capabilities={SEARCH, DETAIL, PAGINATION}")


def test_default_sources_still_naukri_only(failures):
    """Phase 11 update: _default_sources() now includes HIRIST too,
    since it is genuinely ENABLED -- see test above."""
    # Subset, not exact equality: _default_sources() also includes any
    # search-provider-backed source (*_SEARCH) ENABLED in THIS
    # environment's .env, correctly (see search_provider.py's
    # root-cause .env-loading fix). This test's own concern is only
    # that the 4 direct sources are present.
    defaults = search_profile._default_sources()
    if not {"NAUKRI", "HIRIST", "IIMJOBS", "APNA"} <= set(defaults):
        return _fail(failures, f"_default_sources() = {defaults!r}, expected it to include at least {{'NAUKRI', 'HIRIST', 'IIMJOBS', 'APNA'}}")

    print(f"PASS: search_profile._default_sources() includes {{'NAUKRI', 'HIRIST', 'IIMJOBS', 'APNA'}} (got {sorted(defaults)})")


def test_design_doc_normalized_job_fields_are_real(failures):
    """
    Section 5 of the design doc claims a future Hirist adapter would
    map into these exact normalize_job() output keys. Confirm every
    one of them is actually produced today.
    """
    sample_raw_job = {
        "source": "TESTSOURCE",
        "company": "Example Co",
        "title": "Example Title",
        "location": "Bengaluru",
        "work_model": "",
        "job_url": "https://example.com/job/1",
        "application_url": "",
        "posted_date": "",
        "jd_text": "",
        "experience_required": "",
        "mandatory_skills": [],
        "preferred_skills": [],
    }

    normalized = normalize_job(sample_raw_job, 1)

    expected_fields = {
        "source", "company", "title", "location", "work_model",
        "job_url", "application_url", "posted_date", "jd_text",
        "experience_required", "mandatory_skills", "preferred_skills",
        "experience_min_months", "job_id",
    }

    missing = expected_fields - set(normalized.keys())
    if missing:
        return _fail(failures, f"design doc claims these normalize_job() fields exist, but they are missing: {missing}")

    print(f"PASS: all {len(expected_fields)} normalize_job() fields cited in the design doc are real")


def test_design_doc_unmapped_fields_default_safely(failures):
    """
    Section 5 claims unobserved fields (location, work_model, job_url,
    etc.) default to empty-string/empty-list rather than being
    fabricated. Confirm this is the real, current normalize_job()
    behavior for a minimal (company+title-only) input, matching the
    one confirmed-minimal shape Phase 6 actually observed (name +
    position only in the JSON-LD fragment).
    """
    minimal_job = {"source": "HIRIST", "company": "Verint", "title": "Senior DevOps Engineer"}

    try:
        normalized = normalize_job(minimal_job, 1)
    except Exception as error:  # noqa: BLE001
        return _fail(failures, f"a job dict with only company+title failed to normalize: {error}")

    if normalized["location"] != "" or normalized["job_url"] != "" or normalized["posted_date"] != "":
        return _fail(failures, "unobserved fields did not default to empty string as the design doc claims")

    if normalized["mandatory_skills"] != [] or normalized["preferred_skills"] != []:
        return _fail(failures, "unobserved list fields did not default to empty lists as the design doc claims")

    print("PASS: unobserved fields default safely to empty string/list, matching the design doc's Section 5 claims")


def test_design_doc_work_model_enum_values_are_real(failures):
    """
    Section 5 claims an unconfirmed work_model would default toward
    UNKNOWN via the existing WorkModel enum. Confirm the enum's real
    members.
    """
    expected = {"REMOTE", "HYBRID", "ONSITE", "UNKNOWN"}
    actual = {member.name for member in WorkModel}

    if actual != expected:
        return _fail(failures, f"WorkModel enum members changed or mismatched: {actual}")

    print("PASS: WorkModel enum members match what the design doc cites (REMOTE, HYBRID, ONSITE, UNKNOWN)")


def test_design_doc_rawjob_dataclass_fields_are_real(failures):
    """
    Section 1/5 describe RawJob's fields as the adapter output
    contract. Confirm RawJob's actual dataclass fields match.
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
    print("HIRIST PHASE 6 STEP 2 OFFLINE DESIGN GROUNDING TEST")
    print("======================================================")

    failures = []

    test_hirist_still_not_enabled(failures)
    test_default_sources_still_naukri_only(failures)
    test_design_doc_normalized_job_fields_are_real(failures)
    test_design_doc_unmapped_fields_default_safely(failures)
    test_design_doc_work_model_enum_values_are_real(failures)
    test_design_doc_rawjob_dataclass_fields_are_real(failures)

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll Hirist Phase 6 Step 2 offline design grounding tests passed.")


if __name__ == "__main__":
    main()
