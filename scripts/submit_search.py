#!/usr/bin/env python3

"""
CLI to submit a candidate's CONFIRMED search profile as a QUEUED search
run. Executes NO search -- only queues the work (search_runs +
search_queue rows) for a future worker to consume.

Usage:
    python3 scripts/submit_search.py --candidate-id <id>
        [--source SRC ...] [--min-score N] [--max-results N]
        [--max-job-age-days N] [--db PATH] [--confirm]

Without --confirm: shows the planned query count, sources, target
roles/locations, and minimum score -- performs NO database write.

With --confirm: creates the QUEUED search_runs + search_queue rows.

--max-job-age-days is optional (Phase 8, data/reports/phase8_daily_queue_design.md)
-- passed straight through to search_submission.submit_search()'s
existing max_job_age_days parameter, unchanged otherwise. Omitted:
identical to this flag never having existed (unrestricted discovery
freshness, the pre-existing default).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from search_submission import DB_PATH as DEFAULT_DB_PATH
from search_submission import SearchSubmissionError, submit_search


def _parse_args(argv):
    candidate_id = None
    sources = []
    min_score = None
    max_results = None
    max_job_age_days = None
    confirm = False
    db_path = str(DEFAULT_DB_PATH)

    i = 0
    while i < len(argv):
        token = argv[i]

        if token == "--candidate-id":
            if i + 1 >= len(argv):
                print("ERROR: --candidate-id requires a value")
                sys.exit(1)
            candidate_id = argv[i + 1]
            i += 2
        elif token == "--source":
            if i + 1 >= len(argv):
                print("ERROR: --source requires a value")
                sys.exit(1)
            sources.append(argv[i + 1])
            i += 2
        elif token == "--min-score":
            if i + 1 >= len(argv):
                print("ERROR: --min-score requires a value")
                sys.exit(1)
            try:
                min_score = int(argv[i + 1])
            except ValueError:
                print(f"ERROR: --min-score must be an integer, got {argv[i + 1]!r}")
                sys.exit(1)
            i += 2
        elif token == "--max-results":
            if i + 1 >= len(argv):
                print("ERROR: --max-results requires a value")
                sys.exit(1)
            try:
                max_results = int(argv[i + 1])
            except ValueError:
                print(f"ERROR: --max-results must be an integer, got {argv[i + 1]!r}")
                sys.exit(1)
            i += 2
        elif token == "--max-job-age-days":
            if i + 1 >= len(argv):
                print("ERROR: --max-job-age-days requires a value")
                sys.exit(1)
            try:
                max_job_age_days = int(argv[i + 1])
            except ValueError:
                print(f"ERROR: --max-job-age-days must be an integer, got {argv[i + 1]!r}")
                sys.exit(1)
            i += 2
        elif token == "--db":
            if i + 1 >= len(argv):
                print("ERROR: --db requires a path")
                sys.exit(1)
            db_path = argv[i + 1]
            i += 2
        elif token == "--confirm":
            confirm = True
            i += 1
        else:
            print(f"ERROR: unrecognized argument: {token}")
            sys.exit(1)

    if not candidate_id:
        print("ERROR: --candidate-id is required")
        sys.exit(1)

    return candidate_id, (sources or None), min_score, max_results, max_job_age_days, confirm, db_path


def main():
    candidate_id, sources, min_score, max_results, max_job_age_days, confirm, db_path = _parse_args(
        sys.argv[1:]
    )

    try:
        result = submit_search(
            db_path,
            candidate_id,
            sources=sources,
            minimum_match_score=min_score,
            maximum_results=max_results,
            max_job_age_days=max_job_age_days,
            dry_run=not confirm,
        )
    except SearchSubmissionError as error:
        print(f"ERROR: {error}")
        sys.exit(1)

    print("SEARCH SUBMISSION PLAN")
    print("=======================")
    print(f"Candidate ID     : {result.candidate_id}")
    print(f"Target roles     : {', '.join(result.target_roles)}")
    print(f"Target locations : {', '.join(result.target_locations)}")
    print(f"Sources          : {', '.join(result.sources)}")
    print(f"Minimum score    : {result.minimum_match_score}")
    print(f"Planned queries  : {result.query_count}")
    print()

    if not confirm:
        print("This was a PREVIEW only -- no database write was made.")
        print(
            "Re-run with --confirm to create the QUEUED search_run and "
            "search_queue records."
        )
        return

    if result.duplicate_of:
        print(
            f"An identical search is already QUEUED as "
            f"search_run_id={result.duplicate_of}."
        )
        print("No new search_run was created (duplicate-submission policy).")
        return

    print("SUBMITTED")
    print(f"search_run_id : {result.search_run_id}")
    print("Status        : QUEUED")


if __name__ == "__main__":
    main()
