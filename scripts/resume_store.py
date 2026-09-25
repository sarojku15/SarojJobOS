#!/usr/bin/env python3

"""
Resume FILE version records -- item 9's genuinely missing piece found
by this session's audit: the `resumes` table (candidate_id, filename,
content_hash, resume_version, file_path, uploaded_at, parsed_at,
status) has existed in the schema since migrate_v2_schema.py, but
nothing ever wrote a row into it -- api/main.py's upload_resume()
route only ever saved the PDF to disk and extracted profile FIELDS
into candidate_search_profile (a separate, already-working DRAFT ->
CONFIRMED lifecycle -- see candidate_profile.py/profile_store.py,
unaffected by this module). Without a `resumes` row, there was no
queryable record of which resume FILE(s) a candidate has on file, no
resume_id to attach to a candidate_job_matches row, and nothing for
resume_variant_selector.py to select between.

This module is the one place that writes to `resumes`. It records
real, candidate-provided facts only (filename, byte hash, upload/parse
timestamps, parse outcome) -- it never invents a resume_version label;
that column stays NULL unless a future candidate-facing feature lets
someone set one explicitly.

UNIQUE(candidate_id, content_hash) means re-uploading byte-identical
content updates the existing row's parsed_at/status rather than
creating a duplicate "version" -- a genuinely new version requires
genuinely different bytes.
"""

import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def content_hash(file_bytes):
    return hashlib.sha256(file_bytes).hexdigest()


def record_uploaded_resume(conn, candidate_id, filename, file_bytes, file_path, status):
    """
    Insert (or, for a byte-identical re-upload, refresh) one `resumes`
    row for this candidate. status is the caller's own parse outcome
    ("PARSED" or "FAILED") -- never guessed here. Returns the resume_id
    (existing, on a duplicate-content re-upload; freshly generated
    otherwise).

    Uses a cursor-scoped row_factory (not conn.row_factory) so this
    call never mutates the caller's connection-wide default for
    whatever else runs on the same connection afterward.
    """
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    chash = content_hash(file_bytes)
    now = _now()

    existing = cursor.execute(
        "SELECT resume_id FROM resumes WHERE candidate_id = ? AND content_hash = ?",
        (candidate_id, chash),
    ).fetchone()

    if existing is not None:
        conn.execute(
            "UPDATE resumes SET parsed_at = ?, status = ?, file_path = ? "
            "WHERE resume_id = ?",
            (now, status, str(file_path), existing["resume_id"]),
        )
        conn.commit()
        return existing["resume_id"]

    resume_id = f"resume_{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO resumes (
            resume_id, candidate_id, filename, content_hash, resume_version,
            file_path, uploaded_at, parsed_at, parser_version, status
        ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
        """,
        (resume_id, candidate_id, filename, chash, str(file_path), now, now, "resume_extractor_v1", status),
    )
    conn.commit()
    return resume_id


def list_resumes_for_candidate(conn, candidate_id):
    """Every resume version on file for this candidate, most recently
    uploaded first -- used by both a future resume-management UI and
    resume_variant_selector.py. Cursor-scoped row_factory, same
    rationale as record_uploaded_resume() above."""
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    rows = cursor.execute(
        "SELECT resume_id, filename, content_hash, resume_version, uploaded_at, parsed_at, status "
        "FROM resumes WHERE candidate_id = ? ORDER BY uploaded_at DESC",
        (candidate_id,),
    ).fetchall()
    return [dict(row) for row in rows]
