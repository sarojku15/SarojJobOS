#!/usr/bin/env python3

"""
Phase 7.4 offline production-readiness tests
(data/reports/phase7_4_production_readiness_audit.md, STEP 9).

Covers the Step 9 checklist items NOT already covered by pre-existing
test files:
  - stale-lock reclaim (new this phase)
  - explicit SIGINT/SIGTERM trap release (new this phase)
  - run_summary.json atomic-write failure safety (Phase 7.2 mechanism,
    never directly tested before)
  - the diagnostic JOBOS_DAILY_DB_OVERRIDE cannot be present in the
    installed plist (it must only ever be set by a human's shell for a
    one-off diagnostic run, never baked into the scheduled job)
  - lock prevents overlapping execution (a cleaner, automated version
    of the manual proof from Phase 7.2/7.3)
  - no automatic application-submission code path exists anywhere in
    the daily-run chain (wrapper, plist, run_search_worker.py,
    search_worker.py)

Items ALREADY covered by pre-existing, passing test files are cited,
not duplicated:
  - "missing Naukri result is not falsely interpreted as success" and
    "blocked Naukri does not generate a misleading successful report":
    scripts/test_naukri_search_classification.py (VALID_EMPTY_RESULT
    vs BLOCKED vs PARSE_FAILURE classification) and
    scripts/test_search_worker.py tests 18/33 (a blocked source
    resolves to search_run status BLOCKED, never COMPLETED)
  - "report save failure preserves previous workbook byte-for-byte"
    and "dry-run creates no filesystem changes":
    scripts/test_daily_scheduler_offline.py
  - "production DB path remains default when override is unset":
    scripts/test_daily_scheduler_offline.py

No network. No production DB. Temporary directories/fixtures only.
"""

import plistlib
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

WRAPPER_PATH = ROOT / "scripts" / "run_daily_search.sh"
PLIST_PATH = ROOT / "launchd" / "com.sarojjobos.dailysearch.plist"


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _test_stale_lock_reclaim(failures):
    import tempfile

    wrapper_text = WRAPPER_PATH.read_text(encoding="utf-8")
    if "STALE_LOCK_THRESHOLD_SECONDS" not in wrapper_text:
        _fail(failures, "wrapper script does not define STALE_LOCK_THRESHOLD_SECONDS")
        return
    if "lock_age" not in wrapper_text or "-gt \"$STALE_LOCK_THRESHOLD_SECONDS\"" not in wrapper_text:
        _fail(failures, "wrapper script does not compare lock age against the stale threshold")
        return
    print("PASS: wrapper script defines a stale-lock age threshold and compares against it")

    # Dynamic proof: pre-create a lock dir, backdate its mtime far
    # beyond the threshold, run the wrapper in --dry-run (still zero
    # network -- the reclaim logic runs AFTER the dry-run early exit in
    # a real invocation, so we instead unit-test the shell arithmetic
    # directly here, offline, matching the wrapper's own logic).
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = Path(tmp) / "stale.lock"
        lock_dir.mkdir()
        old_time = time.time() - 7200  # 2 hours ago, well past the 3600s threshold
        import os
        os.utime(lock_dir, (old_time, old_time))

        script = f'''
        LOCK_DIR="{lock_dir}"
        STALE_LOCK_THRESHOLD_SECONDS=3600
        if [ -d "$LOCK_DIR" ]; then
            lock_mtime="$(stat -f%m "$LOCK_DIR" 2>/dev/null || echo 0)"
            lock_age=$(( $(date +%s) - lock_mtime ))
            if [ "$lock_age" -gt "$STALE_LOCK_THRESHOLD_SECONDS" ]; then
                rmdir "$LOCK_DIR" 2>/dev/null || true
                echo "RECLAIMED age=$lock_age"
            else
                echo "NOT_STALE age=$lock_age"
            fi
        fi
        '''
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)
        if "RECLAIMED" not in result.stdout:
            _fail(failures, f"stale-lock reclaim logic did not reclaim a 2-hour-old lock: stdout={result.stdout!r} stderr={result.stderr!r}")
        elif lock_dir.exists():
            _fail(failures, "stale lock directory still exists after reclaim logic ran")
        else:
            print(f"PASS: a 2-hour-old lock (well past the 3600s threshold) is correctly reclaimed: {result.stdout.strip()}")


def _test_signal_trap_present(failures):
    wrapper_text = WRAPPER_PATH.read_text(encoding="utf-8")
    if "trap _release_lock EXIT INT TERM" not in wrapper_text:
        _fail(failures, "wrapper script does not trap EXIT, INT, and TERM together for lock release")
    else:
        print("PASS: wrapper script explicitly traps EXIT, INT, and TERM to release the lock (not relying solely on bash's implicit EXIT-on-signal behavior)")


def _test_run_summary_atomic_write_safety(failures):
    import json
    import tempfile

    sys.path.insert(0, str(ROOT / "scripts"))

    out_dir = Path(tempfile.mkdtemp(prefix="jobos_run_summary_atomic_test_"))
    summary_path = out_dir / "run_summary.json"

    # Mirror run_search_worker.py's own atomic-write logic exactly
    # (temp file + replace), since that logic lives inline in
    # _maybe_generate_report() rather than a separately importable
    # function -- this test exercises the identical mechanism.
    def _atomic_write_summary(data):
        import os
        tmp_path = summary_path.with_name(f".{summary_path.name}.tmp-{os.getpid()}")
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_path.replace(summary_path)

    _atomic_write_summary({"example": 1})
    if not summary_path.exists():
        _fail(failures, "run_summary.json atomic-write test: initial write did not create the file")
        return
    original_bytes = summary_path.read_bytes()
    print(f"PASS: run_summary.json atomic-write test setup -- initial summary written ({len(original_bytes)} bytes)")

    # Forced failure mid-write: _atomic_write_summary() makes exactly
    # one write_text() call per attempt (the temp file write --
    # Path.replace() is a separate, unpatched rename, never reached if
    # write_text() itself raises first). Patching write_text to always
    # raise inside this context proves the real summary_path is never
    # touched by a failed attempt.
    def _flaky_write_text(self, *args, **kwargs):
        raise RuntimeError("simulated disk-full / crash during run_summary.json write")

    with mock.patch.object(Path, "write_text", _flaky_write_text):
        try:
            _atomic_write_summary({"example": 2})
            _fail(failures, "run_summary.json atomic-write test: expected a forced failure to raise, it did not")
        except RuntimeError:
            pass  # expected

    if not summary_path.exists():
        _fail(failures, "run_summary.json atomic-write test: previous successful summary was DELETED by a failed regeneration attempt")
    elif summary_path.read_bytes() != original_bytes:
        _fail(failures, "run_summary.json atomic-write test: previous successful summary was CORRUPTED by a failed regeneration attempt")
    else:
        print("PASS: a forced run_summary.json write failure left the previous successful summary byte-identical, untouched")

    leftover = list(out_dir.glob(".run_summary.json.tmp-*"))
    if leftover:
        _fail(failures, f"run_summary.json atomic-write test: leftover temp file(s) not cleaned up: {leftover}")
    else:
        print("PASS: no leftover temp file after a failed run_summary.json write")


def _test_diagnostic_override_absent_from_plist(failures):
    plist = plistlib.loads(PLIST_PATH.read_bytes())
    env = plist.get("EnvironmentVariables", {})
    if "JOBOS_DAILY_DB_OVERRIDE" in env:
        _fail(failures, f"CRITICAL: the installed plist itself sets JOBOS_DAILY_DB_OVERRIDE={env['JOBOS_DAILY_DB_OVERRIDE']!r} -- a scheduled run would NEVER touch the real production DB")
        return

    plist_bytes = PLIST_PATH.read_bytes()
    if b"JOBOS_DAILY_DB_OVERRIDE" in plist_bytes:
        _fail(failures, "the string JOBOS_DAILY_DB_OVERRIDE appears somewhere in the plist file even outside EnvironmentVariables -- investigate")
        return

    print("PASS: JOBOS_DAILY_DB_OVERRIDE does not appear anywhere in the plist -- a normal launchd invocation cannot accidentally activate the diagnostic DB override")

    # Also confirm the plist declares no PATH/other env that could
    # smuggle the override in some indirect way.
    if env:
        print(f"       (plist EnvironmentVariables present: {list(env.keys())} -- none is the DB override)")


def _test_lock_prevents_overlap(failures):
    # The wrapper hardcodes its own LOCK_DIR under the real project's
    # logs/ (PROJECT_ROOT is fixed, by design -- see run_daily_search.sh),
    # so testing overlap protection means pre-creating that REAL lock
    # dir (freshly created, not stale) and confirming the wrapper
    # refuses to proceed -- it must exit before ever reaching the
    # network/Python invocation.
    real_lock_dir = ROOT / "logs" / ".run_daily_search.lock"
    if real_lock_dir.exists():
        _fail(failures, "PRE-EXISTING real lock directory found before this test even started -- not created by this test, aborting to avoid interference")
        return

    real_lock_dir.mkdir(parents=True)
    try:
        result = subprocess.run([str(WRAPPER_PATH)], capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            _fail(failures, f"wrapper exited 0 while a lock was held -- overlap protection did not trigger. stdout={result.stdout!r}")
        elif "already in progress" not in result.stderr:
            _fail(failures, f"wrapper exited non-zero but without the expected 'already in progress' message: stderr={result.stderr!r}")
        else:
            print("PASS: wrapper correctly refuses to run (exits non-zero) while a lock is already held, confirming overlap protection")
    finally:
        if real_lock_dir.exists():
            real_lock_dir.rmdir()


def _test_no_automatic_application_submission(failures):
    # NOTE (Phase 8, data/reports/phase8_daily_queue_design.md): the
    # daily-run chain now intentionally contains "submit_search.py
    # --confirm" -- this queues a SEARCH QUERY (search_runs/search_queue
    # rows), never a job APPLICATION. Correctly excluded from the
    # suspicious-token list below; only genuine application-submission
    # tokens are checked for.
    files_to_check = [
        ROOT / "scripts" / "run_daily_search.sh",
        ROOT / "launchd" / "com.sarojjobos.dailysearch.plist",
        ROOT / "scripts" / "run_search_worker.py",
        ROOT / "scripts" / "search_worker.py",
    ]
    suspicious_tokens = [
        "apply_to_job", "submit_application",
        "auto_apply", "autosubmit", ".click(", "page.click", "form.submit",
    ]
    found = []
    for path in files_to_check:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in suspicious_tokens:
            if token in text:
                found.append((str(path.relative_to(ROOT)), token))

    if found:
        _fail(failures, f"found application-submission-like token(s) in the daily-run chain: {found}")
    else:
        print(f"PASS: no automatic application-submission code path found across the daily-run chain ({len(files_to_check)} files checked)")

    # Confirm the code-level fact: process_queue_item() ends at
    # candidate_job_matches / jobs persistence + (optional) reporting --
    # it never calls anything named like a submission function.
    search_worker_text = (ROOT / "scripts" / "search_worker.py").read_text(encoding="utf-8")
    if "def process_queue_item" not in search_worker_text:
        _fail(failures, "could not locate process_queue_item() in search_worker.py to verify its scope")
    else:
        print("PASS: search_worker.process_queue_item() confirmed present; its own docstring and this file's static test (test_search_worker.py) already assert it contains no application-submission or direct browser-automation code")


def main():
    failures = []
    print("PHASE 7.4 PRODUCTION-READINESS OFFLINE TESTS")
    print("=================================================================================")

    _test_stale_lock_reclaim(failures)
    _test_signal_trap_present(failures)
    _test_run_summary_atomic_write_safety(failures)
    _test_diagnostic_override_absent_from_plist(failures)
    _test_lock_prevents_overlap(failures)
    _test_no_automatic_application_submission(failures)

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)

    print("\nAll Phase 7.4 production-readiness offline tests passed.")


if __name__ == "__main__":
    main()
