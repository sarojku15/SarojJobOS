#!/usr/bin/env python3

"""
Candidate-scoped resume variant selection -- item 10's genuinely
missing piece. Before this module, resume_variant existed only as an
always-empty column on the GLOBAL `jobs` table (see init_tracker.py),
which cannot hold two different values for two different candidates
matched to the same job, and nothing ever wrote to it anyway.

Honest scope, stated plainly: this project's confirmed candidate
profile currently has exactly ONE real, verified resume file on disk
for its one real candidate (resumes/SarojKumarNayak_SRE_DevOps_
11Yrs.pdf -- config/profile.json's own SRE-A/DEVOPS-A/AZURE-A/AWS-A/
PLATFORM-A concept names 5 variants, but 4 of those 5 files do not
exist -- see CLAUDE.md's "Resume Variants" section). This module does
NOT invent tailored content for those 4 missing variants, and does NOT
guess which named variant a candidate's resume "is" from the job's
requirements -- that would fabricate a claim about the resume's
content this system cannot verify.

What it DOES do, honestly: select among a candidate's OWN uploaded
resumes (scripts/resume_store.py) -- today that is normally exactly
one, so selection trivially resolves to "the candidate's only resume
on file," which is still correct, non-fabricated behavior. The
architecture is ready for genuine multi-variant tailoring (multiple
real resume files per candidate, each with its own resume_version
label a candidate sets explicitly) whenever that exists; until then,
the human-facing label is either that explicit resume_version (if the
candidate set one) or, honestly, the resume's own uploaded filename --
never a fabricated SRE-A/DEVOPS-A/... guess.

Never selects across candidates: candidate_id is always required, and
every query is scoped to it.
"""

import sqlite3
from pathlib import Path


def select_resume_for_profile_version(conn, candidate_id, profile_version):
    """
    Returns (resume_id, resume_variant_label) for the EXACT resume that
    produced this candidate's specific profile_version (via
    candidate_search_profile.source_resume_id -- see
    migrate_v8_resume_profile_traceability.py), or (None, None) if that
    version has no resume lineage recorded (e.g. a manually-entered
    profile with no resume upload behind it) -- never falls back to
    "candidate's newest resume" itself; the caller
    (search_worker.upsert_candidate_job_match) decides that fallback
    explicitly so the choice is visible, not hidden in this function.

    This is the traceability-correct counterpart to
    select_resume_for_candidate() above: that function always picks
    the newest resume (correct when no specific version is pinned);
    this one picks the resume tied to a SPECIFIC version (correct when
    a saved search is pinned to a historical profile_version and must
    never silently drift to a newer resume).
    """
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    version_row = cursor.execute(
        "SELECT source_resume_id FROM candidate_search_profile WHERE candidate_id = ? AND version = ?",
        (candidate_id, profile_version),
    ).fetchone()
    if version_row is None or not version_row["source_resume_id"]:
        return None, None

    resume_row = cursor.execute(
        "SELECT resume_id, resume_version, filename FROM resumes WHERE resume_id = ?",
        (version_row["source_resume_id"],),
    ).fetchone()
    if resume_row is None:
        return None, None

    label = resume_row["resume_version"]
    if not label and resume_row["filename"]:
        label = Path(resume_row["filename"]).stem

    return resume_row["resume_id"], label


def select_resume_for_candidate(conn, candidate_id):
    """
    Returns (resume_id, resume_variant_label) for this candidate's most
    recently uploaded, successfully parsed resume, or (None, None) if
    the candidate has no resume on file -- never guessed, never shared
    with any other candidate_id.

    resume_variant_label is resumes.resume_version when the candidate
    explicitly set one, else the resume's own filename (minus
    extension) -- always real, candidate-provided data, never invented
    content or an assumed SRE-A/DEVOPS-A/... mapping.

    Uses a cursor-scoped row_factory (not conn.row_factory) so this
    call never mutates the caller's connection-wide default -- this
    runs deep inside search_worker.py's hot upsert path, where a
    connection-level row_factory change would leak into unrelated
    later queries reusing the same connection.
    """
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    row = cursor.execute(
        "SELECT resume_id, resume_version, filename FROM resumes "
        "WHERE candidate_id = ? AND status = 'PARSED' "
        "ORDER BY uploaded_at DESC LIMIT 1",
        (candidate_id,),
    ).fetchone()

    if row is None:
        return None, None

    label = row["resume_version"]
    if not label and row["filename"]:
        label = Path(row["filename"]).stem

    return row["resume_id"], label
