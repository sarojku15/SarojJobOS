#!/bin/bash
#
# Phase 7.2 daily search wrapper (data/reports/phase7_2_automation_status_audit.md,
# PART B). This is the ONE thing the (not-yet-installed) launchd job
# actually invokes -- it exists because launchd itself has no safe way
# to expand today's date into a path, and because the daily run needs
# overlap protection and a fixed, absolute, project-local Python
# (.venv, where openpyxl is installed) that a bare `python3` in
# launchd's own minimal environment cannot be relied on to resolve.
#
# This script:
#   - computes today's date-based output directory
#     (data/daily/YYYY-MM-DD/)
#   - refuses to run if a previous invocation is still in flight
#     (mkdir-based lock -- portable, atomic, no dependency on `flock`,
#     which is not shipped by default on macOS)
#   - invokes .venv/bin/python3 scripts/submit_search.py --candidate-id
#     saroj --max-job-age-days 3 --confirm (Phase 8 addition -- see
#     data/reports/phase8_daily_queue_design.md) to (idempotently)
#     ensure there is fresh queued work for today, THEN invokes
#     .venv/bin/python3 scripts/run_search_worker.py --once
#     --candidate-id saroj --report-out <today's workbook path>,
#     explicitly against the REAL production DB path (never a temp/
#     test DB) and with headless Chromium explicitly forced on
#   - never deletes, truncates, or renames any file itself -- the
#     workbook's atomic-write safety lives entirely inside
#     generate_run_report.py (see that module's generate_workbook()
#     docstring); this script's only job is to invoke the worker and
#     capture its logs
#   - writes its own detailed log to data/daily/YYYY-MM-DD/worker.log
#     (per PART C's layout) and a short status line to
#     logs/daily_search_scheduler.log (per PART B requirement #7: "all
#     logs under ~/SarojJobOS/logs/")
#   - NEVER submits an application, NEVER touches production DB
#     contents beyond what the read/write-through search_worker
#     pipeline itself already does (this script performs no direct
#     database access)
#
# Usage:
#   scripts/run_daily_search.sh              # real run
#   scripts/run_daily_search.sh --dry-run     # print what WOULD run;
#                                              # touches no lock, no
#                                              # network, no Python at
#                                              # all -- safe to run
#                                              # anytime, including in
#                                              # tests
#
# This script is NOT installed anywhere, NOT registered with launchd,
# and NOT executed automatically by anything in this repository --
# see launchd/README.md for the (manual, human-approved) install step.

set -euo pipefail

# Absolute project path (PART B requirement #3) -- launchd provides no
# working-directory guarantee, so every path here is built from this,
# never a relative path or an inherited $PWD.
PROJECT_ROOT="/Users/sarojnayak/SarojJobOS"

VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python3"

# Phase 7.3 (data/reports/phase7_3_controlled_daily_run.md) diagnostic
# override: PRODUCTION_DB defaults to, and in every installed/scheduled
# run remains, the real production path below -- this line is
# UNCHANGED from Phase 7.2 except for adding the optional
# ${JOBOS_DAILY_DB_OVERRIDE:-...} indirection itself. The override is
# NEVER set by the launchd plist, NEVER set by a default invocation of
# this script, and exists ONLY so a one-off controlled validation run
# can point this exact wrapper/worker/report chain at a disposable
# temp DB instead of production, without touching production
# configuration, without changing what "the production DB path" means,
# and without weakening any safety check -- unset (the default), this
# script's behavior is byte-for-byte identical to Phase 7.2's.
PRODUCTION_DB="${JOBOS_DAILY_DB_OVERRIDE:-$PROJECT_ROOT/data/applications/jobos.db}"
WORKER_SCRIPT="$PROJECT_ROOT/scripts/run_search_worker.py"
LOG_DIR="$PROJECT_ROOT/logs"
SCHEDULER_LOG="$LOG_DIR/daily_search_scheduler.log"
LOCK_DIR="$LOG_DIR/.run_daily_search.lock"
CANDIDATE_ID="saroj"

# Preserve existing headless Chromium behavior explicitly (PART B
# requirement #9) -- naukri_fetch_bridge.js already defaults to
# headless whenever this is unset or anything other than "0", but this
# script sets it explicitly so a scheduled run's behavior never
# silently depends on an inherited/absent environment variable.
export JOBOS_BROWSER_HEADLESS=1

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=1
fi

TODAY="$(date +%Y-%m-%d)"
DAILY_DIR="$PROJECT_ROOT/data/daily/$TODAY"
REPORT_OUT="$DAILY_DIR/job_search_report.xlsx"
WORKER_LOG="$DAILY_DIR/worker.log"

_log_scheduler() {
    mkdir -p "$LOG_DIR"
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" >> "$SCHEDULER_LOG"
}

if [ -n "${JOBOS_DAILY_DB_OVERRIDE:-}" ]; then
    echo "!! DIAGNOSTIC DB OVERRIDE ACTIVE -- using $PRODUCTION_DB instead of the real production DB !!" >&2
fi

if [ "$DRY_RUN" = "1" ]; then
    echo "DRY RUN -- nothing will be executed, no lock taken, no network access."
    echo "Would create directory : $DAILY_DIR"
    echo "Would write log to     : $WORKER_LOG"
    echo "Would append status to : $SCHEDULER_LOG"
    echo "Would run:"
    echo "  1) \"$VENV_PYTHON\" \"$PROJECT_ROOT/scripts/submit_search.py\" --candidate-id $CANDIDATE_ID --max-job-age-days 3 --confirm --db \"$PRODUCTION_DB\""
    echo "  2) JOBOS_BROWSER_HEADLESS=1 \"$VENV_PYTHON\" \"$WORKER_SCRIPT\" --once --candidate-id $CANDIDATE_ID --db \"$PRODUCTION_DB\" --report-out \"$REPORT_OUT\""
    exit 0
fi

mkdir -p "$LOG_DIR" "$DAILY_DIR"

if [ -n "${JOBOS_DAILY_DB_OVERRIDE:-}" ]; then
    _log_scheduler "DIAGNOSTIC DB OVERRIDE ACTIVE: PRODUCTION_DB=$PRODUCTION_DB (NOT the real production DB)"
fi

# Phase 7.4 (data/reports/phase7_4_production_readiness_audit.md,
# STEP 5) stale-lock safety net: a `trap ... EXIT` alone cannot fire on
# SIGKILL, an OOM kill, or a hard machine/power failure -- these are
# the ONE class of failure no shell trap can ever catch, by OS design,
# not an oversight here. Left unhandled, a lock orphaned this way would
# silently block EVERY future scheduled run forever, since nothing
# would ever remove it. STALE_LOCK_THRESHOLD_SECONDS (3600s = 1 hour)
# is far beyond any real run's observed duration (Phase 7.3's one live
# validation: ~3.5 minutes end to end) -- a lock older than this is
# reclaimed automatically, loudly logged, and the run proceeds. This
# does NOT weaken the lock for its actual purpose (preventing two
# genuinely concurrent runs) -- it only prevents a single stale lock
# from permanently disabling all future runs.
STALE_LOCK_THRESHOLD_SECONDS=3600
if [ -d "$LOCK_DIR" ]; then
    lock_mtime="$(stat -f%m "$LOCK_DIR" 2>/dev/null || echo 0)"
    lock_age=$(( $(date +%s) - lock_mtime ))
    if [ "$lock_age" -gt "$STALE_LOCK_THRESHOLD_SECONDS" ]; then
        _log_scheduler "STALE LOCK RECLAIMED: $LOCK_DIR was ${lock_age}s old (threshold ${STALE_LOCK_THRESHOLD_SECONDS}s) -- a prior run likely crashed uncleanly (SIGKILL/OOM/power loss); removing and proceeding"
        rmdir "$LOCK_DIR" 2>/dev/null || true
    fi
fi

# Overlap protection (PART B requirement #8): `mkdir` is atomic on
# every POSIX filesystem -- it fails if the directory already exists,
# which is exactly the property a lock needs, without depending on
# `flock` (not a default macOS utility). The lock is released on every
# exit path this script's own logic can control -- normal completion,
# `set -e` triggering early, AND an explicit SIGINT/SIGTERM (the
# graceful-termination signals launchd or a human `kill` would send;
# added in Phase 7.4 alongside EXIT for an explicit, unambiguous
# guarantee rather than relying on bash's own EXIT-trap-on-signal
# behavior, which is subtler and less portable to depend on silently).
_release_lock() {
    rmdir "$LOCK_DIR" 2>/dev/null || true
}
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    _log_scheduler "SKIPPED: a previous run is still in progress (lock held at $LOCK_DIR)"
    echo "Another run is already in progress (lock: $LOCK_DIR). Exiting without starting a second one." >&2
    exit 1
fi
trap _release_lock EXIT INT TERM

_log_scheduler "START: daily search for candidate=$CANDIDATE_ID, output=$REPORT_OUT"

# Phase 8 (data/reports/phase8_daily_queue_design.md, PART A): the
# daily run must not depend on a human manually calling submit_search()
# between scheduled executions. This reuses the existing, already-
# tested, already-idempotent search_submission.submit_search() (via
# its existing CLI, scripts/submit_search.py -- no new queue logic was
# written, only one new pass-through --max-job-age-days flag added to
# that CLI). Idempotency is entirely inherited, not reimplemented:
# submit_search()'s own duplicate-submission policy (a deterministic
# fingerprint of candidate + profile version + query plan + score/
# result thresholds) means calling this every single day is always
# safe -- an unprocessed QUEUED submission from earlier today is
# detected and reused (no duplicate row), while a COMPLETED/FAILED run
# from a PRIOR day never blocks a fresh one (see
# scripts/test_search_submission.py test 20 for the existing proof of
# this exact property). `sources` is intentionally omitted here (not
# hardcoded to NAUKRI) so this automatically tracks whichever sources
# are AdapterStatus.ENABLED at run time via search_profile._default_sources() --
# Hirist/LinkedIn become included the moment (and only the moment) they
# are actually enabled, with zero change needed here.
#
# This step's own failure (e.g. no CONFIRMED profile yet) is logged
# but does NOT abort the run -- the worker step below is always safe
# to attempt regardless (it simply reports "no eligible QUEUED work
# found" if there is genuinely nothing to process, exactly as it
# always has).
set +e
"$VENV_PYTHON" "$PROJECT_ROOT/scripts/submit_search.py" \
    --candidate-id "$CANDIDATE_ID" \
    --max-job-age-days 3 \
    --confirm \
    --db "$PRODUCTION_DB" \
    >> "$WORKER_LOG" 2>&1
SUBMIT_EXIT_CODE=$?
set -e
_log_scheduler "QUEUE PREP: submit_search.py exit=$SUBMIT_EXIT_CODE (see $WORKER_LOG for detail; a non-zero exit here does not abort this run)"

set +e
"$VENV_PYTHON" "$WORKER_SCRIPT" \
    --once \
    --candidate-id "$CANDIDATE_ID" \
    --db "$PRODUCTION_DB" \
    --report-out "$REPORT_OUT" \
    >> "$WORKER_LOG" 2>&1
WORKER_EXIT_CODE=$?
set -e

if [ "$WORKER_EXIT_CODE" -eq 0 ]; then
    _log_scheduler "SUCCESS: exit=$WORKER_EXIT_CODE workbook=$REPORT_OUT log=$WORKER_LOG"
else
    # PART B requirement #11/#12: a failed run (offline Mac, network
    # unavailable, Naukri blocked, worker crash, workbook-generation
    # failure) exits non-zero and is logged clearly here -- but this
    # script never deletes or overwrites $REPORT_OUT itself, and
    # generate_run_report.py's own atomic write (temp file + rename,
    # only on success) guarantees a partial/failed generation never
    # touches whatever workbook path it was writing to. Today's
    # directory may simply be missing or incomplete after a failure;
    # any PREVIOUS day's directory/workbook is untouched regardless,
    # since a new day always gets its own $DAILY_DIR.
    _log_scheduler "FAILURE: exit=$WORKER_EXIT_CODE -- see $WORKER_LOG for detail. Previous successful workbooks (any prior date) are unaffected."
fi

exit "$WORKER_EXIT_CODE"
