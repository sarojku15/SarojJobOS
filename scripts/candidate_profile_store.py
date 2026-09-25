#!/usr/bin/env python3

"""
Filesystem-based persistence for candidate-profile artifacts.

This is deliberately NOT a database layer: no SQLite, no new tables,
no writes to data/applications/jobos.db. It exists so an extracted (or
manually-entered) CandidateProfile -- of ANY lifecycle status, DRAFT
included -- can be saved to and loaded back from a plain JSON file on
disk, as a stepping stone before a real database-backed store exists.

Reuses, unchanged:
  - candidate_profile.normalize_candidate_profile()  (JSON -> CandidateProfile)
  - candidate_profile.serialize_candidate_profile()  (CandidateProfile -> JSON)
  - candidate_profile.validate_candidate_profile()

There is no second profile schema here -- every field this module
writes or reads is defined once, in candidate_profile.py.

============================================================================
LIFECYCLE SAFETY
============================================================================
save_candidate_profile_draft() and load_candidate_profile_draft() are
PURE I/O: neither one reads, writes, or otherwise touches
profile_status or confirmed_by_user. Whatever lifecycle state a profile
was in when saved (DRAFT, CONFIRMED, or ARCHIVED) is exactly the state
it comes back in when loaded -- saving/loading is never itself a
confirmation step, and there is no code path in this module that can
promote a DRAFT to CONFIRMED. The only sanctioned promotion path
remains candidate_profile.promote_to_confirmed(), called explicitly by
a future review/confirm component -- never from here.
"""

import json
from pathlib import Path

from candidate_profile import (
    CandidateProfile,
    normalize_candidate_profile,
    serialize_candidate_profile,
    validate_candidate_profile,
)


def save_candidate_profile_draft(profile, path):
    """
    Serialize `profile` (via candidate_profile.serialize_candidate_
    profile() -- no second serialization format) and write it as
    indented JSON to `path`, creating parent directories as needed.

    Despite the name (matching this component's requested API), this
    works for a profile of any profile_status, not only DRAFT -- it
    never inspects or changes profile_status. Returns the resolved
    Path written to.
    """
    if not isinstance(profile, CandidateProfile):
        raise ValueError(
            f"save_candidate_profile_draft() expects a CandidateProfile, "
            f"got {type(profile).__name__}"
        )

    data = serialize_candidate_profile(profile)

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    with target.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    return target


def load_candidate_profile_draft(path):
    """
    Read a JSON file previously written by save_candidate_profile_
    draft() (or any JSON matching candidate_profile.py's canonical
    shape) and convert it back into a CandidateProfile via
    candidate_profile.normalize_candidate_profile().

    Raises ValueError -- never returns a partial or default profile --
    on: a missing file, unreadable/corrupt JSON, or JSON that
    normalize_candidate_profile() itself rejects (e.g. missing
    candidate_id, an unparseable enum value). profile_status is
    whatever was saved; loading never confirms a DRAFT.
    """
    source = Path(path)

    if not source.exists():
        raise ValueError(f"Candidate profile file not found: {source}")

    try:
        with source.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Candidate profile file at {source} is not valid JSON: {error}"
        ) from error

    return normalize_candidate_profile(raw)


def validate_candidate_profile_draft(profile):
    """
    Thin re-export of candidate_profile.validate_candidate_profile()
    for API symmetry with save/load -- intentionally does not duplicate
    its logic; see candidate_profile.py for the actual validation
    rules.
    """
    return validate_candidate_profile(profile)
