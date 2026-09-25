#!/usr/bin/env python3

"""
CLI for scripts/search_worker.py's single safe processing primitive.

Usage:
    python3 scripts/run_search_worker.py --once
    python3 scripts/run_search_worker.py --once --max-items 5
    python3 scripts/run_search_worker.py --once --candidate-id saroj
    python3 scripts/run_search_worker.py --once --search-run-id <id>
    python3 scripts/run_search_worker.py --once --dry-run
    python3 scripts/run_search_worker.py --once --db /path/to/test.db
    python3 scripts/run_search_worker.py --once --candidate-id saroj --report-out data/reports/daily_run_report.xlsx

There is no daemon/loop mode -- this always runs exactly one bounded
cycle (up to --max-items queue items, default 1) and exits. A future
scheduler is expected to invoke this repeatedly.

============================================================================
--report-out: OPT-IN reporting integration (Phase 7.1)
============================================================================
Added per data/reports/phase7_1_reporting_semantics_audit.md, Question 3
("is generate_run_report.py invoked automatically after a normal search
run?"). Before this flag existed, the answer was unconditionally NO --
no code anywhere called generate_run_report.py, and no scheduler/daemon
of any kind exists yet in this project (confirmed by tracing the code,
not assumed).

This flag is the identified integration point, made real: when passed,
AFTER run_once() completes (successfully or not -- see below),
generate_run_report.generate() is called against the SAME --db and
--candidate-id already used for the run, writing the workbook to the
given path. It requires --candidate-id (a single-item run against one
candidate's queue) -- report generation is skipped, with a clear
message, for a --dry-run (nothing was claimed/processed) or if no
--candidate-id was given (the report needs one candidate to score
against; scoring is not global).

This does NOT make the workbook part of any automatic/scheduled
pipeline -- it remains an explicit, opt-in CLI flag a human (or a
FUTURE scheduler, once one exists) must pass. Nothing about
run_once()'s own behavior, search/scoring/eligibility/freshness/dedup
logic, or the production DB's CONTENTS is changed by this flag --
report generation is read-only. Requires openpyxl (see requirements.txt);
only imported when --report-out is actually passed, so this CLI's
normal (non-reporting) usage is completely unaffected and needs no venv.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from search_worker import DB_PATH as DEFAULT_DB_PATH
from search_worker import run_once


def _parse_args(argv):
    once = False
    max_items = 1
    candidate_id = None
    search_run_id = None
    dry_run = False
    db_path = str(DEFAULT_DB_PATH)
    report_out = None
    report_since = None

    i = 0
    while i < len(argv):
        token = argv[i]

        if token == "--once":
            once = True
            i += 1
        elif token == "--max-items":
            if i + 1 >= len(argv):
                print("ERROR: --max-items requires a value")
                sys.exit(1)
            try:
                max_items = int(argv[i + 1])
            except ValueError:
                print(f"ERROR: --max-items must be an integer, got {argv[i + 1]!r}")
                sys.exit(1)
            i += 2
        elif token == "--candidate-id":
            if i + 1 >= len(argv):
                print("ERROR: --candidate-id requires a value")
                sys.exit(1)
            candidate_id = argv[i + 1]
            i += 2
        elif token == "--search-run-id":
            if i + 1 >= len(argv):
                print("ERROR: --search-run-id requires a value")
                sys.exit(1)
            search_run_id = argv[i + 1]
            i += 2
        elif token == "--dry-run":
            dry_run = True
            i += 1
        elif token == "--db":
            if i + 1 >= len(argv):
                print("ERROR: --db requires a path")
                sys.exit(1)
            db_path = argv[i + 1]
            i += 2
        elif token == "--report-out":
            if i + 1 >= len(argv):
                print("ERROR: --report-out requires a path")
                sys.exit(1)
            report_out = argv[i + 1]
            i += 2
        elif token == "--report-since":
            if i + 1 >= len(argv):
                print("ERROR: --report-since requires an ISO datetime")
                sys.exit(1)
            report_since = argv[i + 1]
            i += 2
        else:
            print(f"ERROR: unrecognized argument: {token}")
            sys.exit(1)

    if not once:
        print("ERROR: --once is required (this worker has no daemon/loop mode)")
        sys.exit(1)

    if report_out and not candidate_id:
        print("ERROR: --report-out requires --candidate-id (a report is scored against one candidate)")
        sys.exit(1)

    return max_items, candidate_id, search_run_id, dry_run, db_path, report_out, report_since


def _print_result(result):
    print(f"queue_id={result.queue_id} search_run_id={result.search_run_id} candidate_id={result.candidate_id}")
    print(f"  status               : {result.status}")

    if result.status == "SKIPPED":
        print(f"  (dry-run preview -- {result.unique_count} quer{'y' if result.unique_count == 1 else 'ies'} would be executed, nothing claimed or written)")
        return

    if result.max_job_age_days_requested is not None:
        print(f"  freshness constraint  : ≤ {result.max_job_age_days_requested} days (source-native discovery filter)")
        print(f"  jobs beyond constraint: {result.jobs_exceeding_max_age} (ideally 0 when the source's native filter is working)")

    print(f"  raw results          : {result.raw_count}")
    print(f"  malformed             : {result.malformed_count}")
    print(f"  normalized            : {result.normalized_count}")
    print(f"  duplicates removed    : {result.duplicate_count}")
    print(f"  unique results        : {result.unique_count}")
    print(f"  inserted jobs (global): {result.inserted_jobs}")
    print(f"  existing jobs (global): {result.existing_jobs}")
    print(f"  excluded (experience) : {result.excluded_experience}")
    print(f"  excluded (location)   : {result.excluded_location}")
    print(f"  eligible              : {result.eligible_count}")
    print(f"  scored                : {result.scored_count}")
    print(f"  ready for approval    : {result.ready_count}")
    print(f"  matches created       : {result.matches_created}")
    print(f"  matches updated       : {result.matches_updated}")

    if result.unimplemented_sources:
        print(f"  unimplemented sources : {result.unimplemented_sources}")
    if result.blocked_sources:
        print(f"  blocked sources       : {result.blocked_sources}")
    if result.timed_out_queries:
        print(f"  timed-out queries     : {result.timed_out_queries}")
    if result.errors:
        print(f"  errors ({len(result.errors)}):")
        for error in result.errors[:5]:
            print(f"    - {error}")


def _maybe_generate_report(db_path, candidate_id, report_out, report_since, results, dry_run):
    """
    Opt-in only -- see this module's docstring, "--report-out" section.
    Never called unless --report-out was explicitly passed. Skips
    (with a clear message, not silently) for --dry-run or when nothing
    was actually claimed this cycle, since there is then no new state
    for a report to usefully reflect beyond what a prior run already
    showed.
    """
    if not report_out:
        return

    if dry_run:
        print("\n--report-out skipped: --dry-run claims/processes nothing, so there is nothing new to report.")
        return

    if not results:
        print("\n--report-out skipped: no queue item was claimed this cycle.")
        return

    import generate_run_report as grr

    since = report_since if report_since is not None else grr._default_since()
    report = grr.generate(db_path, candidate_id, report_out, since=since)

    # Phase 7.2 PART C: run_summary.json lives next to the workbook, in
    # the same directory -- written with the same atomic
    # temp-file-then-rename discipline as the workbook itself (see
    # generate_run_report.generate_workbook()'s docstring), so a
    # partial/failed write can never corrupt a previous day's summary.
    import json as _json

    summary_path = Path(report_out).with_name("run_summary.json")
    tmp_summary_path = summary_path.with_name(f".{summary_path.name}.tmp-{__import__('os').getpid()}")
    try:
        tmp_summary_path.write_text(_json.dumps(report["summary"], indent=2, default=str), encoding="utf-8")
        tmp_summary_path.replace(summary_path)
    except Exception:
        tmp_summary_path.unlink(missing_ok=True)
        raise

    print()
    print("REPORT GENERATED (opt-in --report-out)")
    print("========================================")
    print(f"Workbook       : {report['workbook_path']}")
    print(f"Run summary    : {summary_path}")
    summary = report["summary"]
    print(f"APPLY_TODAY    : {summary['apply_today_count']}")
    print(f"NEW_JOBS       : {summary['new_jobs_count']}")
    print(f"ALREADY_APPLIED: {summary['already_applied_count']}")
    print(f"REJECTED       : {summary['rejected_excluded_count']}")


def main():
    max_items, candidate_id, search_run_id, dry_run, db_path, report_out, report_since = _parse_args(sys.argv[1:])

    print("SEARCH WORKER")
    print("=============")
    print(f"DB          : {db_path}")
    print(f"Max items   : {max_items}")
    print(f"Candidate   : {candidate_id or '(any)'}")
    print(f"Search run  : {search_run_id or '(any)'}")
    print(f"Dry run     : {dry_run}")
    if report_out:
        print(f"Report out  : {report_out}")
    print()

    results = run_once(
        db_path,
        candidate_id=candidate_id,
        search_run_id=search_run_id,
        max_items=max_items,
        dry_run=dry_run,
    )

    if not results:
        print("No eligible QUEUED work found.")
        _maybe_generate_report(db_path, candidate_id, report_out, report_since, results, dry_run)
        return

    for result in results:
        _print_result(result)
        print()

    print(f"Processed {len(results)} item(s).")

    _maybe_generate_report(db_path, candidate_id, report_out, report_since, results, dry_run)


if __name__ == "__main__":
    main()
