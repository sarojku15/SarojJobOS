#!/usr/bin/env python3

"""
Persistence layer for Custom Job Scoring Profiles (scripts/
scoring_profiles.py does the actual scoring math; this module only
reads/writes the scoring_profiles table added by
scripts/migrate_v17_scoring_profiles.py).

Ownership: every function requires an explicit candidate_id, exactly
the same convention as resume_store.py/company_research.py/
application_follow_ups.py -- this is the existing browser-localStorage
candidate_id scoping, NOT real authentication (see docs/SECURITY.md).
A profile row belongs to exactly one candidate_id; every read/write
here filters by it, so one candidate can never read, edit, or delete
another candidate's profile through this module.

"is_default": at most one TRUE per candidate is enforced here (not by
a DB constraint) -- the same pattern candidate_search_profile.is_active
already uses. Setting a new default clears the previous one in the
same transaction.

Versioning: editing a profile increments `version` and updates
`updated_at` -- the row itself is mutated in place (there is no
separate history table), but any job match scored with an OLDER
version has already captured {scoring_profile_id, scoring_profile_version}
on the match row itself (migrate_v17), so a historical score remains
interpretable ("this was profile X, version 2, which at the time had
these weights") even though the live profile row has since moved to
version 3. Reconstructing version 2's exact weights is NOT supported
by this minimal implementation -- see docs/SCORING.md's "Known
limitations" for this explicitly stated gap.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from scoring_profiles import CRITERIA, ScoringProfileError, validate_scoring_profile


class ScoringProfileNotFoundError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row):
    return {
        "scoring_profile_id": row["scoring_profile_id"],
        "candidate_id": row["candidate_id"],
        "name": row["name"],
        "description": row["description"],
        "weights": json.loads(row["weights_json"]),
        "mandatory_gate_enabled": bool(row["mandatory_gate_enabled"]),
        "min_shortlist_score": row["min_shortlist_score"],
        "is_default": bool(row["is_default"]),
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _validate_payload(name, weights, mandatory_gate_enabled, min_shortlist_score):
    if not name or not str(name).strip():
        raise ScoringProfileError("A scoring profile must have a non-empty name.")
    # Re-use the exact same validation scoring_profiles.py applies at
    # evaluation time, so an invalid profile can never be SAVED in the
    # first place -- never a separate, looser "save-time" check.
    validate_scoring_profile({"weights": weights, "min_shortlist_score": min_shortlist_score})


def list_profiles(conn, candidate_id):
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM scoring_profiles WHERE candidate_id = ? ORDER BY created_at ASC",
        (candidate_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_profile(conn, candidate_id, scoring_profile_id):
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM scoring_profiles WHERE candidate_id = ? AND scoring_profile_id = ?",
        (candidate_id, scoring_profile_id),
    ).fetchone()
    if row is None:
        raise ScoringProfileNotFoundError(
            f"No scoring profile {scoring_profile_id!r} for candidate {candidate_id!r}"
        )
    return _row_to_dict(row)


def get_default_profile(conn, candidate_id):
    """The candidate's current default profile dict, or None if they
    have none set (meaning: use legacy scoring)."""
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM scoring_profiles WHERE candidate_id = ? AND is_default = 1",
        (candidate_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def _clear_existing_default(conn, candidate_id):
    conn.execute(
        "UPDATE scoring_profiles SET is_default = 0 WHERE candidate_id = ? AND is_default = 1",
        (candidate_id,),
    )


def create_profile(conn, candidate_id, name, weights, description=None,
                    mandatory_gate_enabled=True, min_shortlist_score=70, is_default=False):
    _validate_payload(name, weights, mandatory_gate_enabled, min_shortlist_score)

    scoring_profile_id = f"sp_{uuid.uuid4().hex[:12]}"
    now = _now()

    if is_default:
        _clear_existing_default(conn, candidate_id)

    conn.execute(
        """
        INSERT INTO scoring_profiles (
            scoring_profile_id, candidate_id, name, description, weights_json,
            mandatory_gate_enabled, min_shortlist_score, is_default, version,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            scoring_profile_id, candidate_id, str(name).strip(), description,
            json.dumps(weights), int(bool(mandatory_gate_enabled)),
            float(min_shortlist_score), int(bool(is_default)), now, now,
        ),
    )
    conn.commit()
    return get_profile(conn, candidate_id, scoring_profile_id)


def update_profile(conn, candidate_id, scoring_profile_id, name=None, weights=None,
                    description=None, mandatory_gate_enabled=None,
                    min_shortlist_score=None, is_default=None):
    existing = get_profile(conn, candidate_id, scoring_profile_id)  # 404s if not owned

    new_name = name if name is not None else existing["name"]
    new_weights = weights if weights is not None else existing["weights"]
    new_min_score = min_shortlist_score if min_shortlist_score is not None else existing["min_shortlist_score"]
    new_gate = mandatory_gate_enabled if mandatory_gate_enabled is not None else existing["mandatory_gate_enabled"]
    new_description = description if description is not None else existing["description"]

    _validate_payload(new_name, new_weights, new_gate, new_min_score)

    if is_default is True:
        _clear_existing_default(conn, candidate_id)

    conn.execute(
        """
        UPDATE scoring_profiles
        SET name = ?, description = ?, weights_json = ?, mandatory_gate_enabled = ?,
            min_shortlist_score = ?, is_default = ?, version = version + 1, updated_at = ?
        WHERE candidate_id = ? AND scoring_profile_id = ?
        """,
        (
            str(new_name).strip(), new_description, json.dumps(new_weights),
            int(bool(new_gate)), float(new_min_score),
            int(bool(is_default)) if is_default is not None else int(existing["is_default"]),
            _now(), candidate_id, scoring_profile_id,
        ),
    )
    conn.commit()
    return get_profile(conn, candidate_id, scoring_profile_id)


def delete_profile(conn, candidate_id, scoring_profile_id):
    get_profile(conn, candidate_id, scoring_profile_id)  # 404s if not owned
    conn.execute(
        "DELETE FROM scoring_profiles WHERE candidate_id = ? AND scoring_profile_id = ?",
        (candidate_id, scoring_profile_id),
    )
    conn.commit()


def duplicate_profile(conn, candidate_id, scoring_profile_id, new_name):
    source = get_profile(conn, candidate_id, scoring_profile_id)  # 404s if not owned
    return create_profile(
        conn, candidate_id, new_name, source["weights"],
        description=source["description"],
        mandatory_gate_enabled=source["mandatory_gate_enabled"],
        min_shortlist_score=source["min_shortlist_score"],
        is_default=False,  # a duplicate is never auto-made default
    )
