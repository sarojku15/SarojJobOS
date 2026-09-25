#!/usr/bin/env python3

"""
Production candidate_search_profile reconciliation.

Synchronizes ONE candidate's existing candidate_search_profile row (by
candidate_id) so its profile_json holds the serialized canonical
CandidateProfile (candidate_profile.py's schema) instead of any legacy
or otherwise stale shape. This module is intentionally narrow:

  - It never builds, extracts, edits, or confirms a profile itself --
    it only loads an ALREADY-CONFIRMED profile JSON file (produced by
    the existing resume_extractor.py + review_candidate_profile.py
    workflow) and writes it into the DB.
  - It touches exactly one table: candidate_search_profile. It never
    modifies candidates, jobs, candidate_job_matches, search_runs, or
    search_queue.
  - It updates the candidate's EXISTING row in place when one exists
    (preserving that row's version, created_at, and profile_id) --
    it never creates a second row for the same candidate_id. Only
    when no row exists at all for this candidate does it insert one
    (version=1).
  - It performs no search, no network, no browser call of any kind.

Reuses, unchanged:
  - candidate_profile.py (CandidateProfile, ProfileStatus,
    normalize_candidate_profile, serialize_candidate_profile,
    validate_candidate_profile)
  - candidate_profile_store.py (load_candidate_profile_draft -- despite
    its name, it loads a profile of any lifecycle status)
  - search_submission.py (build_search_plan, read-only, used only to
    VERIFY -- never to submit -- that the synchronized profile can now
    produce a search plan)

No second profile schema, no manually-constructed JSON dict is written
to SQL -- every byte written to profile_json comes from
candidate_profile.serialize_candidate_profile().
"""

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "applications" / "jobos.db"

sys.path.insert(0, str(ROOT / "scripts"))

from candidate_profile import ProfileStatus, serialize_candidate_profile
from candidate_profile_store import load_candidate_profile_draft
from search_submission import SearchSubmissionError, build_search_plan


class SyncError(ValueError):
    """Raised for any reason a candidate_search_profile row cannot be
    safely synchronized: DRAFT profile, unconfirmed profile, candidate_id
    mismatch, unknown candidate, etc."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def is_legacy_profile_shape(raw):
    """
    True if `raw` (an already-parsed JSON object, e.g. from an existing
    candidate_search_profile.profile_json column) looks like the OLD
    flat config/profile.json shape rather than the new canonical
    candidate_profile.py schema -- i.e. it has no "identity" key (the
    new schema's required top-level key) but does have the old shape's
    telltale "candidate" key. Used only for REPORTING what is about to
    be replaced -- never to auto-convert or merge the two shapes.
    """
    if not isinstance(raw, dict):
        return False
    return "identity" not in raw and "candidate" in raw


def load_profile_to_sync(profile_path, expected_candidate_id):
    """
    Load the profile JSON at `profile_path` (via the existing
    candidate_profile_store loader -- no second deserializer) and
    verify it is safe to synchronize:
      - profile_status must be CONFIRMED (a DRAFT is refused)
      - confirmed_by_user must be True (an internally-inconsistent
        profile is refused)
      - identity.candidate_id must equal `expected_candidate_id`
        exactly (a profile for a different candidate is refused,
        never silently retargeted)
    Raises SyncError otherwise. Never mutates the loaded profile.
    """
    profile = load_candidate_profile_draft(profile_path)

    if profile.metadata.profile_status != ProfileStatus.CONFIRMED:
        raise SyncError(
            f"Profile at {profile_path} is "
            f"{profile.metadata.profile_status.value}, not CONFIRMED -- "
            f"a DRAFT (or ARCHIVED) profile cannot be synchronized to "
            f"production"
        )

    if not profile.metadata.confirmed_by_user:
        raise SyncError(
            f"Profile at {profile_path} has profile_status=CONFIRMED but "
            f"confirmed_by_user=False -- refusing an internally "
            f"inconsistent profile"
        )

    if profile.identity.candidate_id != expected_candidate_id:
        raise SyncError(
            f"Profile at {profile_path} has candidate_id="
            f"{profile.identity.candidate_id!r}, expected "
            f"{expected_candidate_id!r} -- refusing to synchronize a "
            f"profile for a different candidate"
        )

    return profile


def inspect_existing_row(conn, candidate_id):
    """
    Read-only: returns (candidate_row, profile_row) for `candidate_id`,
    each None if absent. candidate_row = (candidate_id, status).
    profile_row = (profile_id, version, profile_json, confirmed_by_user,
    is_active, created_at, updated_at) for the row with is_active=1 (or
    the highest version if none is marked active), or None if this
    candidate has no candidate_search_profile row at all.
    """
    candidate_row = conn.execute(
        "SELECT candidate_id, status FROM candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()

    profile_row = conn.execute(
        """
        SELECT profile_id, version, profile_json, confirmed_by_user, is_active, created_at, updated_at
        FROM candidate_search_profile
        WHERE candidate_id = ?
        ORDER BY is_active DESC, version DESC
        LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()

    return candidate_row, profile_row


def describe_change(candidate_id, profile, existing_profile_row):
    """
    Build a plain-text, human-readable description of exactly what
    will change -- used by the CLI's preview (no --confirm-production)
    and printed again before the real write. Never performs any I/O
    itself.
    """
    lines = []
    lines.append(f"Candidate ID     : {candidate_id}")

    if existing_profile_row is None:
        lines.append("Existing row     : NONE -- a new candidate_search_profile row will be INSERTED (version=1)")
    else:
        profile_id, version, old_json, old_confirmed, old_active, created_at, updated_at = existing_profile_row
        try:
            old_raw = json.loads(old_json)
        except json.JSONDecodeError:
            old_raw = None

        old_shape = "UNPARSEABLE JSON"
        if old_raw is not None:
            old_shape = "LEGACY flat shape" if is_legacy_profile_shape(old_raw) else "canonical CandidateProfile shape"

        lines.append(f"Existing row     : profile_id={profile_id}, version={version} (UNCHANGED)")
        lines.append(f"Existing shape   : {old_shape}")
        lines.append(f"Existing state   : confirmed_by_user={bool(old_confirmed)}, is_active={bool(old_active)}")
        lines.append(f"Existing created_at (UNCHANGED): {created_at}")

    lines.append("New shape        : canonical CandidateProfile schema (identity/professional_summary/skills/.../metadata)")
    lines.append(f"New profile_status: {profile.metadata.profile_status.value}")
    lines.append(f"New confirmed_by_user: {profile.metadata.confirmed_by_user}")
    lines.append(f"New is_active    : True")
    lines.append(f"Target roles     : {', '.join(profile.job_preferences.target_roles) or '(none)'}")
    lines.append(f"Target locations : {', '.join(profile.job_preferences.target_locations) or '(none)'}")

    return "\n".join(lines)


def sync_candidate_profile(db_path, candidate_id, profile_path, confirm_production=False):
    """
    Full synchronization. Always performs the read-only inspection and
    builds the change description; only performs the write when
    confirm_production=True.

    Returns a dict: {"would_change": bool, "description": str,
    "written": bool, "profile": CandidateProfile}.

    Raises SyncError for: unknown candidate, a profile that fails
    load_profile_to_sync()'s checks. Uses one transaction for the
    write; rolls back completely on any failure, leaving the existing
    row (if any) exactly as it was.
    """
    profile = load_profile_to_sync(profile_path, candidate_id)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        candidate_row, existing_profile_row = inspect_existing_row(conn, candidate_id)

        if candidate_row is None:
            raise SyncError(
                f"Unknown candidate: {candidate_id!r} -- no row in the "
                f"candidates table. This tool never creates a candidate "
                f"row; that is out of scope."
            )

        description = describe_change(candidate_id, profile, existing_profile_row)

        if not confirm_production:
            return {
                "would_change": True,
                "description": description,
                "written": False,
                "profile": profile,
            }

        now = _now()
        new_profile_json = json.dumps(serialize_candidate_profile(profile))

        conn.execute("BEGIN")

        if existing_profile_row is None:
            conn.execute(
                """
                INSERT INTO candidate_search_profile
                    (candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at)
                VALUES (?, 1, 'PROFILE', ?, 1, 1, ?, ?)
                """,
                (candidate_id, new_profile_json, now, now),
            )
        else:
            profile_id = existing_profile_row[0]
            conn.execute(
                """
                UPDATE candidate_search_profile
                SET profile_json = ?, confirmed_by_user = 1, is_active = 1, updated_at = ?
                WHERE profile_id = ?
                """,
                (new_profile_json, now, profile_id),
            )

        conn.commit()

        return {
            "would_change": True,
            "description": description,
            "written": True,
            "profile": profile,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def verify_search_submission_ready(db_path, candidate_id):
    """
    Read-only post-write verification: confirms
    search_submission.build_search_plan() can now successfully build a
    search plan for `candidate_id` (i.e. the synchronized profile is
    genuinely usable, not just structurally saved). NEVER submits a
    search -- no search_runs/search_queue row is created here.

    Returns (ok: bool, message: str).
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        search_profile, query_plan = build_search_plan(conn, candidate_id)
        return True, (
            f"search_submission.build_search_plan() succeeded: "
            f"{len(query_plan)} quer{'y' if len(query_plan) == 1 else 'ies'} "
            f"would be planned (roles={search_profile.target_roles}, "
            f"locations={search_profile.target_locations})"
        )
    except SearchSubmissionError as error:
        return False, f"search_submission.build_search_plan() still fails: {error}"
    finally:
        conn.close()


def _parse_args(argv):
    candidate_id = None
    profile_path = None
    db_path = str(DB_PATH)
    confirm_production = False

    i = 0
    while i < len(argv):
        token = argv[i]

        if token == "--candidate-id":
            if i + 1 >= len(argv):
                print("ERROR: --candidate-id requires a value")
                sys.exit(1)
            candidate_id = argv[i + 1]
            i += 2
        elif token == "--profile":
            if i + 1 >= len(argv):
                print("ERROR: --profile requires a path")
                sys.exit(1)
            profile_path = argv[i + 1]
            i += 2
        elif token == "--db":
            if i + 1 >= len(argv):
                print("ERROR: --db requires a path")
                sys.exit(1)
            db_path = argv[i + 1]
            i += 2
        elif token == "--confirm-production":
            confirm_production = True
            i += 1
        else:
            print(f"ERROR: unrecognized argument: {token}")
            sys.exit(1)

    if not candidate_id:
        print("ERROR: --candidate-id is required")
        sys.exit(1)

    if not profile_path:
        print("ERROR: --profile is required")
        sys.exit(1)

    return candidate_id, profile_path, db_path, confirm_production


def main():
    candidate_id, profile_path, db_path, confirm_production = _parse_args(sys.argv[1:])

    before_exists = Path(db_path).exists()
    before_stat = None
    if before_exists:
        before_bytes = Path(db_path).read_bytes()
        before_stat = {
            "size": len(before_bytes),
            "mtime": Path(db_path).stat().st_mtime,
        }
        before_stat["sha256"] = hashlib.sha256(before_bytes).hexdigest()

    print("CANDIDATE SEARCH PROFILE SYNCHRONIZATION")
    print("=========================================")
    if before_stat:
        print(f"DB before: size={before_stat['size']} mtime={before_stat['mtime']} sha256={before_stat['sha256']}")
    print()

    try:
        result = sync_candidate_profile(db_path, candidate_id, profile_path, confirm_production=confirm_production)
    except SyncError as error:
        print(f"ERROR: {error}")
        sys.exit(1)

    print(result["description"])
    print()

    if not confirm_production:
        print("This was a PREVIEW only -- no database write was made.")
        print("Re-run with --confirm-production to perform this update.")
        return

    if result["written"]:
        after_bytes = Path(db_path).read_bytes()
        after_sha256 = hashlib.sha256(after_bytes).hexdigest()
        print("WRITE COMPLETE")
        print(f"DB after: size={len(after_bytes)} mtime={Path(db_path).stat().st_mtime} sha256={after_sha256}")
        print()

        ok, message = verify_search_submission_ready(db_path, candidate_id)
        print(f"Post-write search-submission verification: {'OK' if ok else 'STILL FAILING'}")
        print(f"  {message}")


if __name__ == "__main__":
    main()
