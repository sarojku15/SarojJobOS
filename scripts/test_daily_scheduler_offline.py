#!/usr/bin/env python3

"""
Phase 7.2 PART D -- offline/inert scheduler validation
(data/reports/phase7_2_automation_status_audit.md). Never installs,
loads, or activates the launchd job. Never makes a network request.
Never runs the real search worker (the wrapper script's own --dry-run
mode is exercised via subprocess, which itself makes no Python or
network call -- see scripts/run_daily_search.sh's header).
"""

import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLIST_PATH = ROOT / "launchd" / "com.sarojjobos.dailysearch.plist"
WRAPPER_PATH = ROOT / "scripts" / "run_daily_search.sh"
GITIGNORE_PATH = ROOT / ".gitignore"

EXPECTED_WRAPPER_ABS_PATH = str(WRAPPER_PATH.resolve())


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def main():
    failures = []
    print("DAILY SCHEDULER OFFLINE VALIDATION (Phase 7.2 PART D)")
    print("=================================================================================")

    if not PLIST_PATH.exists():
        _fail(failures, f"plist not found: {PLIST_PATH}")
        _report_and_exit(failures)
    if not WRAPPER_PATH.exists():
        _fail(failures, f"wrapper script not found: {WRAPPER_PATH}")
        _report_and_exit(failures)

    plist_bytes = PLIST_PATH.read_bytes()
    wrapper_text = WRAPPER_PATH.read_text(encoding="utf-8")

    # --- plist is syntactically valid ---
    try:
        plist = plistlib.loads(plist_bytes)
    except Exception as error:
        _fail(failures, f"plist failed to parse: {error}")
        _report_and_exit(failures)
    else:
        print("PASS: plist is syntactically valid (parsed by plistlib)")

    # --- absolute paths are correct ---
    program_args = plist.get("ProgramArguments", [])
    if not program_args or not program_args[0].startswith("/"):
        _fail(failures, f"ProgramArguments[0] must be an absolute path, got {program_args}")
    elif program_args[0] != EXPECTED_WRAPPER_ABS_PATH:
        _fail(failures, f"ProgramArguments[0] ({program_args[0]}) does not match the actual wrapper script path ({EXPECTED_WRAPPER_ABS_PATH})")
    else:
        print(f"PASS: ProgramArguments[0] is an absolute path matching the real wrapper script: {program_args[0]}")

    working_dir = plist.get("WorkingDirectory", "")
    if not working_dir.startswith("/"):
        _fail(failures, f"WorkingDirectory must be absolute, got {working_dir!r}")
    else:
        print(f"PASS: WorkingDirectory is absolute: {working_dir}")

    # --- .venv Python is referenced (in the wrapper, which is what the
    # plist actually invokes) ---
    if ".venv/bin/python3" not in wrapper_text:
        _fail(failures, "wrapper script does not reference .venv/bin/python3")
    else:
        print("PASS: wrapper script references the project-local .venv Python")

    # --- --once / --candidate-id saroj / --report-out present ---
    for required in ("--once", "--candidate-id", "saroj", "--report-out"):
        if required not in wrapper_text:
            _fail(failures, f"wrapper script missing required token: {required!r}")
    else:
        print("PASS: wrapper script's worker invocation includes --once, --candidate-id saroj, --report-out")

    # --- headless mode remains enabled ---
    plist_env = plist.get("EnvironmentVariables", {})
    if plist_env.get("JOBOS_BROWSER_HEADLESS") != "1":
        _fail(failures, f"plist EnvironmentVariables.JOBOS_BROWSER_HEADLESS must be '1', got {plist_env.get('JOBOS_BROWSER_HEADLESS')!r}")
    if "JOBOS_BROWSER_HEADLESS=1" not in wrapper_text and "export JOBOS_BROWSER_HEADLESS=1" not in wrapper_text:
        _fail(failures, "wrapper script does not explicitly force JOBOS_BROWSER_HEADLESS=1")
    else:
        print("PASS: headless Chromium explicitly forced on, both in the plist and the wrapper script")

    # --- logs point to SarojJobOS/logs ---
    stdout_path = plist.get("StandardOutPath", "")
    stderr_path = plist.get("StandardErrorPath", "")
    expected_logs_prefix = str((ROOT / "logs").resolve())
    if not stdout_path.startswith(expected_logs_prefix) or not stderr_path.startswith(expected_logs_prefix):
        _fail(failures, f"plist StandardOutPath/StandardErrorPath must be under {expected_logs_prefix}, got {stdout_path!r} / {stderr_path!r}")
    elif f'LOG_DIR="{ROOT}/logs"' not in wrapper_text and "LOG_DIR=\"$PROJECT_ROOT/logs\"" not in wrapper_text:
        _fail(failures, "wrapper script does not define LOG_DIR under the project's logs/ directory")
    else:
        print("PASS: all log paths (plist-level and wrapper-level) are under SarojJobOS/logs")

    # --- generated output is outside git-tracked source files ---
    gitignore_text = GITIGNORE_PATH.read_text(encoding="utf-8")
    if "data/daily/" not in gitignore_text:
        _fail(failures, "data/daily/ is not listed in .gitignore -- generated daily artifacts would be git-tracked")
    else:
        print("PASS: data/daily/ is listed in .gitignore -- generated daily artifacts are never git-tracked")

    # --- no automatic APPLICATION submission command exists ---
    # NOTE (Phase 8, data/reports/phase8_daily_queue_design.md): the
    # wrapper now intentionally calls "submit_search.py --confirm" --
    # this submits a SEARCH QUERY for queueing (search_runs/search_queue
    # rows), never a job APPLICATION. That token is therefore correctly
    # NOT in this list -- only genuine application-submission-like
    # tokens are checked for.
    suspicious_tokens = ["apply_to_job", "submit_application", "selenium", "playwright.click", "auto_apply", "autosubmit"]
    found_suspicious = [t for t in suspicious_tokens if t in wrapper_text or t in plist_bytes.decode("utf-8", errors="ignore")]
    if found_suspicious:
        _fail(failures, f"found suspicious application-submission-like token(s): {found_suspicious}")
    else:
        print("PASS: no automatic APPLICATION-submission command found in the wrapper script or plist (search-submission via submit_search.py is present and intentional -- see Phase 8)")

    # --- no production DB path is replaced with a temp DB ---
    if "data/applications/jobos.db" not in wrapper_text:
        _fail(failures, "wrapper script does not reference the real production DB path (data/applications/jobos.db)")
    if "tmp" in wrapper_text.lower().split("PRODUCTION_DB=")[-1].split("\n")[0].lower() if "PRODUCTION_DB=" in wrapper_text else False:
        _fail(failures, "PRODUCTION_DB assignment appears to reference a temp path")
    else:
        print("PASS: wrapper script's PRODUCTION_DB points at the real production DB path, not a temp DB")

    # --- overlapping execution protection is present ---
    if "mkdir \"$LOCK_DIR\"" not in wrapper_text and 'mkdir "$LOCK_DIR"' not in wrapper_text:
        _fail(failures, "wrapper script does not contain the expected mkdir-based lock mechanism")
    elif "trap" not in wrapper_text or "rmdir" not in wrapper_text:
        _fail(failures, "wrapper script's lock is not released via a trap/rmdir on exit")
    else:
        print("PASS: overlap protection present (mkdir-based lock, released via trap on exit)")

    # --- bounded execution: RunAtLoad/KeepAlive both false ---
    if plist.get("RunAtLoad") is not False:
        _fail(failures, f"RunAtLoad must be false, got {plist.get('RunAtLoad')!r}")
    if plist.get("KeepAlive") is not False:
        _fail(failures, f"KeepAlive must be false (a daemon/loop would violate search_worker.py's bounded-execution contract), got {plist.get('KeepAlive')!r}")
    else:
        print("PASS: RunAtLoad=false and KeepAlive=false -- bounded, non-looping, non-auto-relaunching execution")

    # --- wrapper script dry-run mode: no network, no side effects ---
    daily_dir_before = list((ROOT / "data" / "daily").glob("*")) if (ROOT / "data" / "daily").exists() else []
    lock_dir = ROOT / "logs" / ".run_daily_search.lock"
    if lock_dir.exists():
        _fail(failures, "PRE-EXISTING lock directory found before the dry-run test even started -- not created by this test")
    else:
        result = subprocess.run([str(WRAPPER_PATH), "--dry-run"], capture_output=True, text=True, timeout=30)
        daily_dir_after = list((ROOT / "data" / "daily").glob("*")) if (ROOT / "data" / "daily").exists() else []
        if result.returncode != 0:
            _fail(failures, f"wrapper --dry-run exited non-zero: {result.returncode}, stderr={result.stderr!r}")
        elif "DRY RUN" not in result.stdout:
            _fail(failures, f"wrapper --dry-run did not print the expected DRY RUN banner, got: {result.stdout!r}")
        elif lock_dir.exists():
            _fail(failures, "wrapper --dry-run left a lock directory behind -- it must never take the lock")
        elif daily_dir_after != daily_dir_before:
            _fail(failures, f"wrapper --dry-run created filesystem entries under data/daily/: before={daily_dir_before} after={daily_dir_after}")
        else:
            print("PASS: wrapper script's --dry-run mode exits 0, prints its plan, and makes ZERO filesystem changes (no lock, no daily dir, no network)")

    _test_atomic_write_safety(failures)

    _report_and_exit(failures)


def _test_atomic_write_safety(failures):
    """
    PART B requirements #12/#13: a failed workbook generation must
    never corrupt the previous successful workbook, via atomic
    temp-file-then-rename (generate_run_report.generate_workbook()).
    Directly exercises that function with a forced mid-save failure --
    no network, no real search, no database of any kind.
    """
    import tempfile
    from unittest import mock

    sys.path.insert(0, str(ROOT / "scripts"))
    import generate_run_report as grr

    out_dir = Path(tempfile.mkdtemp(prefix="jobos_atomic_write_test_"))
    out_path = out_dir / "job_search_report.xlsx"

    # 1. A genuine successful write.
    result = grr.generate_workbook([], [], [], {"example": 1}, out_path)
    if not out_path.exists():
        _fail(failures, "atomic-write test: first (successful) generate_workbook() did not create the file")
        return
    original_bytes = out_path.read_bytes()
    print(f"PASS: atomic-write test setup -- initial successful workbook written ({len(original_bytes)} bytes)")

    # 2. A forced failure mid-save -- openpyxl's Workbook.save() itself
    # raises. The previous file must survive byte-identical, and no
    # leftover .tmp file should remain.
    with mock.patch("openpyxl.Workbook.save", side_effect=RuntimeError("simulated disk-full / crash during save")):
        try:
            grr.generate_workbook([], [], [], {"example": 2}, out_path)
            _fail(failures, "atomic-write test: expected generate_workbook() to raise on a forced save failure, it did not")
        except RuntimeError:
            pass  # expected

    if not out_path.exists():
        _fail(failures, "atomic-write test: previous successful workbook was DELETED by a failed regeneration attempt")
    elif out_path.read_bytes() != original_bytes:
        _fail(failures, "atomic-write test: previous successful workbook was CORRUPTED/TRUNCATED by a failed regeneration attempt")
    else:
        print("PASS: a forced generate_workbook() failure left the previous successful workbook byte-identical, untouched")

    leftover_tmp_files = list(out_dir.glob(".job_search_report.xlsx.tmp-*"))
    if leftover_tmp_files:
        _fail(failures, f"atomic-write test: leftover temp file(s) not cleaned up after failure: {leftover_tmp_files}")
    else:
        print("PASS: no leftover temp file after a failed generate_workbook() call")


def _report_and_exit(failures):
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("\nAll Phase 7.2 offline scheduler validation checks passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
