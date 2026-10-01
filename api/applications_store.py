"""
"My Applications" -- a candidate-wide view across ALL saved
searches/search runs (2026-09-28 application-tracker implementation).

Root cause this fixes (read-only audit finding): every existing
results view (GET /api/searches/{id}/results) is scoped to ONE saved
search. There was no way to see "everything I've ever shortlisted/
approved/applied to" in one place -- a candidate had to revisit every
search's own results page individually.

This module queries candidate_job_matches JOIN jobs directly --
candidate-wide, not run/search-scoped -- reusing already-persisted
columns exactly as they are (fit_score/priority/resume_variant/
candidate_status/follow_up_date/notes/applied_at/...), never
recomputing eligibility or re-scoring (that stays results_store.py's
job for a single search's live view). This is deliberately the
simpler, "minimum needed" query the audit itself recommended, not a
second copy of results_store.py's fuller freshness/dedup pipeline.
"""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import application_follow_ups as follow_ups_mod

# Statuses that represent a genuinely "found but not yet acted on"
# state -- excluded from the default (no filter) applications list,
# same idea as the existing dashboard/report's own ACTIVE_STATUSES
# concept (scripts/generate_run_report.py), so "My Applications"
# doesn't drown a candidate in every eligible-but-untouched match.
# A candidate can still see these explicitly via ?status=FOUND.
_DEFAULT_EXCLUDED_STATUSES = frozenset({"FOUND", "NOT_QUALIFIED"})


def _row_to_application_dict(row, today_str):
    entry = dict(row)
    if entry.get("follow_up_date"):
        entry.update(follow_ups_mod.compute_due_state(entry["follow_up_date"], today_str))
    else:
        entry["is_overdue"] = False
        entry["is_due_today"] = False
        entry["is_due"] = False
        entry["is_upcoming"] = False
    return entry


def list_applications(
    conn,
    candidate_id,
    status=None,
    company=None,
    source=None,
    follow_up_state=None,
    date_from=None,
    date_to=None,
):
    """
    Every candidate_job_matches row for this candidate, across every
    search/run, joined to its job's own real facts. Never exposes
    another candidate's rows (candidate_id is always the WHERE-clause
    anchor, never optional).

    status: exact candidate_status match (e.g. "APPLIED"). Omitted ->
        every status EXCEPT _DEFAULT_EXCLUDED_STATUSES (see above).
    company / source: case-insensitive substring / exact match.
    follow_up_state: one of "overdue" / "due_today" / "due" /
        "upcoming" / "none" (no follow-up currently scheduled).
    date_from / date_to: ISO date strings, filtered against applied_at
        (falls back to created_at when applied_at is NULL, so an
        unapplied-but-shortlisted job is still date-filterable by when
        it was first matched).
    """
    conn.row_factory = sqlite3.Row

    where = ["cjm.candidate_id = ?"]
    params = [candidate_id]

    if status:
        where.append("cjm.candidate_status = ?")
        params.append(status)
    else:
        placeholders = ",".join("?" for _ in _DEFAULT_EXCLUDED_STATUSES)
        where.append(f"cjm.candidate_status NOT IN ({placeholders})")
        params.extend(_DEFAULT_EXCLUDED_STATUSES)

    if company:
        where.append("j.company LIKE ?")
        params.append(f"%{company}%")

    if source:
        where.append("j.source = ?")
        params.append(source)

    if date_from:
        where.append("COALESCE(cjm.applied_at, cjm.created_at) >= ?")
        params.append(date_from)

    if date_to:
        where.append("COALESCE(cjm.applied_at, cjm.created_at) <= ?")
        params.append(date_to)

    query = f"""
        SELECT
            cjm.candidate_id, cjm.job_id, cjm.search_run_id,
            cjm.fit_score, cjm.priority, cjm.candidate_status,
            cjm.resume_id, cjm.resume_variant,
            cjm.applied_at, cjm.applied_resume_id, cjm.applied_resume_variant,
            cjm.notes,
            cjm.follow_up_date, cjm.created_at, cjm.updated_at,
            j.source, j.company, j.title, j.location, j.work_model,
            j.job_url, j.application_url
        FROM candidate_job_matches cjm
        JOIN jobs j ON j.job_id = cjm.job_id
        WHERE {" AND ".join(where)}
        ORDER BY COALESCE(cjm.applied_at, cjm.updated_at) DESC
    """
    rows = conn.execute(query, params).fetchall()

    today_str = datetime.now(timezone.utc).date().isoformat()

    applications = [_row_to_application_dict(row, today_str) for row in rows]

    if follow_up_state:
        if follow_up_state == "none":
            applications = [a for a in applications if not a["follow_up_date"]]
        else:
            key = f"is_{follow_up_state}"
            applications = [a for a in applications if a.get(key)]

    return applications


def get_applications_summary(applications):
    """Summary counts for the My Applications page's cards -- computed
    purely from the already-fetched list (no second query), so the
    counts and the list can never disagree."""
    total = len(applications)
    applied = sum(1 for a in applications if a["applied_at"] is not None)
    overdue = sum(1 for a in applications if a["is_overdue"])
    due_today = sum(1 for a in applications if a["is_due_today"])
    interviews = sum(
        1 for a in applications
        if a["candidate_status"] in ("RECRUITER_CONTACTED", "SCREENING_CALL", "INTERVIEW_1", "INTERVIEW_2", "FINAL_ROUND")
    )
    awaiting_response = sum(1 for a in applications if a["candidate_status"] == "APPLIED")
    rejected = sum(1 for a in applications if a["candidate_status"] in ("EMPLOYER_REJECTED", "REJECTED", "GHOSTED"))
    offers = sum(1 for a in applications if a["candidate_status"] == "OFFER")

    return {
        "total_applications": total,
        "applied": applied,
        "follow_ups_due": due_today + overdue,
        "overdue": overdue,
        "interviews": interviews,
        "awaiting_response": awaiting_response,
        "rejected": rejected,
        "offers": offers,
    }
