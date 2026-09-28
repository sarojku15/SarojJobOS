#!/usr/bin/env python3
"""
Centralized guard against destructive operations on the two protected
SQLite files in this repository:

    data/applications/jobos.db       (production)
    data/applications/jobos_dev.db   (real dev DB)

Root-cause context (hardening pass): a prior session's own agent ran
manual shell commands (`rm -f data/applications/jobos_dev.db*`) as a
habitual "clean slate before testing" step, twice, outside of any test
file -- this repository's actual test suite was independently audited
and confirmed to contain ZERO code that deletes, unlinks, truncates,
or overwrites either protected path (every test uses its own isolated
`tempfile.mkdtemp()` database; see scripts/test_dev_db_migration_drift.py,
scripts/test_e2e_workflow.py, and every other scripts/test_*.py file).
This module exists as defense-in-depth against a repeat of that same
class of mistake -- by a human, an agent, or a future script -- not as
a fix to a pre-existing code defect (none was found).

This does NOT protect against non-destructive, idempotent operations
(e.g. init_dev_db.init_dev_db() against the real jobos_dev.db is
exactly what the normal dev server already does on every fresh clone
via api/db.py's own ensure_dev_db() -- CREATE TABLE IF NOT EXISTS /
ADD COLUMN-equivalent, never data loss). It exists specifically for
DESTRUCTIVE operations: deleting the file, truncating it, or
overwriting it wholesale (e.g. copying another DB on top of it).

Usage -- call this FIRST, before any destructive operation on a
SQLite file path a script/test computes at runtime:

    from db_safety import assert_safe_to_destroy
    assert_safe_to_destroy(some_path)
    some_path.unlink()  # or shutil.copy2(src, some_path), etc.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRODUCTION_DB = (ROOT / "data" / "applications" / "jobos.db").resolve()
DEV_DB = (ROOT / "data" / "applications" / "jobos_dev.db").resolve()

PROTECTED_DB_PATHS = {PRODUCTION_DB, DEV_DB}


class ProtectedDatabaseError(RuntimeError):
    pass


def assert_safe_to_destroy(path):
    """
    Raises ProtectedDatabaseError with a loud, unambiguous message if
    `path` resolves to the real production or dev database file (or
    one of its SQLite sidecar files: -wal, -shm, -journal). Returns
    the resolved Path silently (no exception) for any other path --
    including every legitimate disposable test path this repository's
    test suite already uses (tempfile.mkdtemp(), etc.).

    There is deliberately no bypass flag, environment variable, or
    parameter that disables this check -- a genuinely intentional
    reset of the real dev DB is done by simply not calling this
    function (i.e. a human explicitly choosing to run a raw shell
    command themselves), never by passing some override through code
    a test could also reach.
    """
    resolved = Path(path).resolve()
    # A SQLite sidecar file (e.g. jobos_dev.db-wal) shares its base
    # database's protection -- strip a known suffix before comparing.
    base = resolved
    for suffix in ("-wal", "-shm", "-journal"):
        if base.name.endswith(suffix):
            base = base.with_name(base.name[: -len(suffix)])
            break

    if base in PROTECTED_DB_PATHS:
        raise ProtectedDatabaseError(
            f"REFUSING DESTRUCTIVE TEST OPERATION ON PROTECTED DATABASE: {resolved}\n"
            f"This path resolves to a protected file ({base}). Destructive "
            f"operations (delete/truncate/overwrite) on the real production "
            f"or dev database are never permitted from test/automation code. "
            f"If you genuinely intend to reset the real dev DB, do so "
            f"explicitly and manually outside of any script that calls this "
            f"guard."
        )
    return resolved


def is_protected(path):
    """Non-raising check, for callers that want to branch instead of
    catching an exception."""
    try:
        assert_safe_to_destroy(path)
        return False
    except ProtectedDatabaseError:
        return True


def disposable_db_path(prefix="jobos_test_"):
    """
    Convenience for any NEW test that needs a fresh, guaranteed-safe
    SQLite file path -- formalizes the tempfile.mkdtemp() pattern every
    scripts/test_*.py file already hand-rolls, and runs the result
    through assert_safe_to_destroy() itself (belt-and-suspenders; a
    tempfile path can never collide with a protected path, but this
    keeps the guard as the single source of truth for "is this path
    safe to create/delete freely" rather than trusting that by
    convention alone).
    """
    import tempfile

    tmp_dir = Path(tempfile.mkdtemp(prefix=prefix))
    path = tmp_dir / "test.db"
    assert_safe_to_destroy(path)
    return path
