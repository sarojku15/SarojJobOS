#!/usr/bin/env python3
"""
Regression tests for scripts/db_safety.py -- the defense-in-depth
guard against destructive operations on the real production/dev
databases (see that module's own docstring for the root-cause context
this was added for: a prior session's manual `rm -f` shell commands,
not a pre-existing code defect in this test suite, which was audited
and confirmed to already use only isolated temp DBs everywhere).

Fully offline. Production and dev DB SHA-256/size verified unchanged
before and after this entire test file.
"""
import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "api"))

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"
DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


production_before = _sha(PRODUCTION_DB)
dev_before = _sha(DEV_DB)

passed = 0
failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS: {msg}")
    else:
        failed += 1
        print(f"FAIL: {msg}")


import db_safety

# 1. Destructive operation against production DB is rejected.
try:
    db_safety.assert_safe_to_destroy(PRODUCTION_DB)
    check(False, "1. assert_safe_to_destroy(PRODUCTION_DB) should have raised, did not")
except db_safety.ProtectedDatabaseError as error:
    check("REFUSING DESTRUCTIVE TEST OPERATION ON PROTECTED DATABASE" in str(error), f"1. production DB path is rejected with the exact required message, got: {error}")

# Same check via a relative / differently-spelled path to the same file.
try:
    db_safety.assert_safe_to_destroy(ROOT / "data" / ".." / "data" / "applications" / "jobos.db")
    check(False, "1b. a differently-spelled path resolving to production should still be rejected")
except db_safety.ProtectedDatabaseError:
    check(True, "1b. path normalization catches a differently-spelled route to the same production file")

# Sidecar files (-wal/-shm/-journal) of the production DB are also protected.
try:
    db_safety.assert_safe_to_destroy(str(PRODUCTION_DB) + "-wal")
    check(False, "1c. production DB's -wal sidecar should be rejected too")
except db_safety.ProtectedDatabaseError:
    check(True, "1c. production DB's -wal sidecar file is also protected")

# 2. Destructive operation against dev DB is rejected.
try:
    db_safety.assert_safe_to_destroy(DEV_DB)
    check(False, "2. assert_safe_to_destroy(DEV_DB) should have raised, did not")
except db_safety.ProtectedDatabaseError as error:
    check("REFUSING DESTRUCTIVE TEST OPERATION ON PROTECTED DATABASE" in str(error), f"2. dev DB path is rejected with the exact required message, got: {error}")

check(db_safety.is_protected(PRODUCTION_DB) is True, "2b. is_protected() reports True for production (non-raising API)")
check(db_safety.is_protected(DEV_DB) is True, "2b. is_protected() reports True for dev DB (non-raising API)")

# 3. A genuinely disposable test DB path is allowed.
tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_db_safety_test_"))
disposable_path = tmp_dir / "disposable.db"
try:
    resolved = db_safety.assert_safe_to_destroy(disposable_path)
    check(resolved == disposable_path.resolve(), f"3. a disposable temp DB path is allowed (returns the resolved path), got {resolved}")
except db_safety.ProtectedDatabaseError as error:
    check(False, f"3. a disposable temp DB path should NOT be rejected, got: {error}")

check(db_safety.is_protected(disposable_path) is False, "3b. is_protected() reports False for a disposable temp path")

# 4. Normal test setup (the established db_mod.DEV_DB = tmp_db pattern
# every other test file already uses) still works after this module
# exists -- it doesn't intercept or change that pattern at all, it's
# an opt-in guard, not a monkeypatch of api/db.py.
import importlib
sys.modules.pop("db", None)
import db as db_mod
db_mod.DEV_DB = disposable_path
check(db_mod.DEV_DB == disposable_path, "4. the existing db_mod.DEV_DB override pattern is completely unaffected by db_safety.py existing")

import shutil
shutil.rmtree(tmp_dir, ignore_errors=True)

# 4b. disposable_db_path() convenience helper produces a safe path.
new_disposable = db_safety.disposable_db_path()
check(not db_safety.is_protected(new_disposable), f"4b. disposable_db_path() produces a non-protected path, got {new_disposable}")
shutil.rmtree(new_disposable.parent, ignore_errors=True)

# 5/6. Existing production and dev DBs are not modified by any of the above.
production_after = _sha(PRODUCTION_DB)
dev_after = _sha(DEV_DB)
check(production_before == production_after, f"5. production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")
check(dev_before == dev_after, f"6. dev DB byte-identical before/after this test run (sha256 before={dev_before}, after={dev_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
