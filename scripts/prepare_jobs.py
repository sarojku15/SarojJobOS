#!/usr/bin/env python3

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data/applications/jobos.db"

sys.path.insert(0, str(ROOT / "scripts"))

from discover_local import load_jobs, normalize_job, deduplicate
from filter_jobs import filter_new_jobs
from score_job import score_job, PROFILE
from job_eligibility import assess_job_eligibility
from source_adapter import SearchQuery
from source_registry import discover_from_sources


JD_EXCERPT_CHARS = 200


def _prepare_from_raw_jobs(raw_jobs, candidate_profile):
    """
    Shared core: normalize -> deduplicate -> filter (read-only against
    the tracker DB) -> job_eligibility.assess_job_eligibility() (the
    shared experience + location eligibility gate) -> score. Used by
    both the file-input mode and the live-source mode so neither
    duplicates this logic.

    candidate_profile is always supplied explicitly by the caller (see
    prepare_jobs()/prepare_jobs_live() below, which both resolve Saroj's
    profile themselves) -- this function has no Saroj-specific default,
    the same convention score_job() and assess_job_eligibility() use, so
    a future multi-candidate caller can reuse this function unchanged by
    simply passing a different candidate_profile.

    A job assess_job_eligibility() marks ineligible (BELOW_PROFILE
    experience and/or NO_MATCH location) is excluded from the normal
    `prepared`/scored list -- it is not scored at all, per the
    eligibility-before-scoring pipeline order. It is never silently
    dropped, though: it is returned in `excluded_experience` and/or
    `excluded_location` (a job failing both gates appears in both, each
    with its own accurate reason -- see job_eligibility.py). Every other
    combination (MATCH, BORDERLINE, ABOVE_PROFILE for experience; MATCH
    for location; and -- crucially -- UNKNOWN for either) remains a
    normal, visible, scored match; UNKNOWN is never a rejection.
    """
    normalized = [
        normalize_job(job, index)
        for index, job in enumerate(raw_jobs, start=1)
    ]

    unique_jobs, duplicate_count = deduplicate(normalized)

    new_jobs, already_tracked = filter_new_jobs(
        unique_jobs
    )

    prepared = []
    excluded_experience = []
    excluded_location = []

    for job in new_jobs:
        eligibility_result = assess_job_eligibility(job, candidate_profile)

        if not eligibility_result.eligible:
            if eligibility_result.reason_code in (
                "EXPERIENCE_BELOW_PROFILE",
                "EXPERIENCE_AND_LOCATION_INELIGIBLE",
            ):
                excluded_experience.append(
                    {
                        "job": job,
                        "experience_assessment": eligibility_result.experience_assessment,
                    }
                )

            if eligibility_result.reason_code in (
                "LOCATION_NO_MATCH",
                "EXPERIENCE_AND_LOCATION_INELIGIBLE",
            ):
                excluded_location.append(
                    {
                        "job": job,
                        "location_assessment": eligibility_result.location_assessment,
                    }
                )

            continue

        scoring = score_job(
            job,
            candidate_profile,
            experience_assessment=eligibility_result.experience_assessment,
        )

        prepared.append(
            {
                "job": job,
                "scoring": scoring,
                "experience_assessment": eligibility_result.experience_assessment,
                "location_assessment": eligibility_result.location_assessment,
            }
        )

    return {
        "raw_count": len(raw_jobs),
        "duplicate_count": duplicate_count,
        "unique_count": len(unique_jobs),
        "already_tracked_count": len(already_tracked),
        "new_count": len(new_jobs),
        "excluded_experience_count": len(excluded_experience),
        "excluded_location_count": len(excluded_location),
        "prepared": prepared,
        "excluded_experience": excluded_experience,
        "excluded_location": excluded_location,
    }


def prepare_jobs(input_path, candidate_profile=PROFILE):
    raw_jobs = load_jobs(input_path)

    return _prepare_from_raw_jobs(raw_jobs, candidate_profile)


def prepare_jobs_live(source, role, location, candidate_profile=PROFILE):
    """
    Zero-write dry run against a real, implemented source adapter.

    Restricted to exactly one source and exactly one query: this never
    invokes the full 320-query planner matrix, and only NAUKRI is
    currently supported (the only source with an implemented
    adapter -- see source_registry.ADAPTERS). Passing any other source
    raises ValueError rather than silently doing nothing or falling
    back to an unimplemented adapter.

    This function does not import or call tracker.upsert_job()
    anywhere, directly or indirectly:
      - discover_from_sources() only calls the adapter's own
        search()/health_check() -- pure discovery, no DB access at all.
      - filter_new_jobs() only ever executes SELECT statements against
        the tracker DB to check what's already tracked; it opens its
        own read-only connection and never writes.
      - assess_job_eligibility() and score_job() are pure computation
        over an in-memory dict.
    There is no code path in this function capable of writing to
    data/applications/jobos.db.
    """
    source = source.upper()

    if source != "NAUKRI":
        raise ValueError(
            f"Live-source mode currently supports only NAUKRI, got '{source}'"
        )

    query = SearchQuery(role=role, location=location)
    raw_jobs = discover_from_sources([(source, query)])

    return _prepare_from_raw_jobs(raw_jobs, candidate_profile)


def _parse_live_args(argv):
    """
    Parse --source/--role/--location from argv. Returns
    (source, role, location) if --source is present, else None (so
    the caller falls back to file-input mode).
    """
    if "--source" not in argv:
        return None

    values = {}
    i = 0

    while i < len(argv):
        if argv[i] in ("--source", "--role", "--location"):
            key = argv[i][2:]

            if i + 1 >= len(argv):
                print(f"ERROR: {argv[i]} requires a value")
                sys.exit(1)

            values[key] = argv[i + 1]
            i += 2
        else:
            i += 1

    missing = [
        flag for flag in ("source", "role", "location")
        if flag not in values
    ]

    if missing:
        print(
            "ERROR: live-source mode requires --source, --role, and "
            "--location (missing: "
            + ", ".join(f"--{m}" for m in missing)
            + ")"
        )
        sys.exit(1)

    return values["source"], values["role"], values["location"]


def _display(value):
    """
    Presentation-only helper: show 'N/A' for an empty/missing value
    rather than an empty string or empty list. Never derives or
    guesses a value -- only formats what is already present (or
    already absent) in the existing job/scoring data.
    """
    if not value:
        return "N/A"

    return value


def _print_prepared_job(job, scoring, experience_assessment, location_assessment):
    """
    Surface fields that already exist on the normalized job dict
    (from discover_local.normalize_job()), the scoring dict (from
    score_job.score_job()), the experience assessment (from
    experience_eligibility.assess_experience_eligibility()), and the
    location assessment (from
    location_taxonomy.assess_location_eligibility()) -- no new
    computation, no new fetch.
    """
    print(
        f"  {job['source']} | "
        f"{job['job_id']} | "
        f"{job['company']} | "
        f"{job['title']} | "
        f"{scoring['score']} | "
        f"{scoring['priority']} | "
        f"{scoring['status']}"
    )

    print(f"    Location    : {_display(job.get('location'))}")
    print(f"    Experience  : {_display(job.get('experience_required'))}")
    print(
        f"    Exp. fit    : {experience_assessment.eligibility.value} "
        f"-- {experience_assessment.reason}"
    )
    print(
        f"    Loc. fit    : {location_assessment.eligibility.value} "
        f"(work model: {location_assessment.work_model.value}) "
        f"-- {location_assessment.reason}"
    )
    print(f"    Job URL     : {_display(job.get('job_url'))}")

    matched = scoring.get("matched_skills") or []
    missing = scoring.get("missing_skills") or []
    hard_reject = scoring.get("hard_reject_reasons") or []

    print(f"    Matched     : {_display(', '.join(matched))}")
    print(f"    Missing     : {_display(', '.join(missing))}")
    print(f"    Hard-reject : {_display('; '.join(hard_reject))}")

    jd_text = (job.get("jd_text") or "").strip()

    if jd_text:
        excerpt = jd_text[:JD_EXCERPT_CHARS]

        if len(jd_text) > JD_EXCERPT_CHARS:
            excerpt += "..."
    else:
        excerpt = "N/A"

    print(f"    JD excerpt  : {excerpt}")

    print()


def _print_excluded_experience_job(job, experience_assessment):
    """
    Excluded-experience jobs are never silently dropped: this prints
    the same identifying fields as a normal prepared job plus the exact
    exclusion reason, so the system can explain e.g. "Excluded: required
    experience 5-10 years, candidate has 11 years."
    """
    print(
        f"  {job['source']} | "
        f"{job['job_id']} | "
        f"{job['company']} | "
        f"{job['title']}"
    )
    print(f"    Experience  : {_display(job.get('experience_required'))}")
    print(f"    Reason      : {experience_assessment.reason}")
    print()


def _print_excluded_location_job(job, location_assessment):
    """
    Excluded-location jobs are never silently dropped either: same
    pattern as _print_excluded_experience_job(), showing the exact
    NO_MATCH reason from location_taxonomy.assess_location_eligibility().
    """
    print(
        f"  {job['source']} | "
        f"{job['job_id']} | "
        f"{job['company']} | "
        f"{job['title']}"
    )
    print(f"    Location    : {_display(job.get('location'))}")
    print(f"    Reason      : {location_assessment.reason}")
    print()


def main():
    argv = sys.argv[1:]

    live_args = _parse_live_args(argv)

    if live_args is not None:
        source, role, location = live_args

        try:
            result = prepare_jobs_live(source, role, location)
        except ValueError as error:
            print(f"ERROR: {error}")
            sys.exit(1)

    else:
        if len(argv) != 1:
            print("Usage:")
            print(
                "  python3 scripts/prepare_jobs.py <jobs.json>"
            )
            print(
                "  python3 scripts/prepare_jobs.py --source NAUKRI "
                "--role \"<role>\" --location \"<location>\""
            )
            sys.exit(1)

        path = ROOT / argv[0]

        if not path.exists():
            print(f"ERROR: file not found: {path}")
            sys.exit(1)

        result = prepare_jobs(path)

    print("Job preparation result")
    print("======================")
    print(f"Raw jobs          : {result['raw_count']}")
    print(f"Duplicates        : {result['duplicate_count']}")
    print(f"Unique jobs       : {result['unique_count']}")
    print(f"Already tracked   : {result['already_tracked_count']}")
    print(f"New jobs          : {result['new_count']}")
    print(f"Excluded (exp.)   : {result['excluded_experience_count']}")
    print(f"Excluded (loc.)   : {result['excluded_location_count']}")
    print()

    if result["prepared"]:
        print("Prepared jobs:")
        print()

        for item in result["prepared"]:
            _print_prepared_job(
                item["job"],
                item["scoring"],
                item["experience_assessment"],
                item["location_assessment"],
            )

    if result["excluded_experience"]:
        print("Excluded jobs (required experience below candidate profile):")
        print()

        for item in result["excluded_experience"]:
            _print_excluded_experience_job(
                item["job"], item["experience_assessment"]
            )

    if result["excluded_location"]:
        print("Excluded jobs (location does not match candidate profile):")
        print()

        for item in result["excluded_location"]:
            _print_excluded_location_job(
                item["job"], item["location_assessment"]
            )


if __name__ == "__main__":
    main()
