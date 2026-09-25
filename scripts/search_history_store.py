#!/usr/bin/env python3

"""
Local, advisory search-history tracking for the daily search planner
(Phase 14.6).

Deliberately NOT the production job/application data model: reads and
writes ONE small JSON file (data/applications/search_query_history.json),
git-ignored by the existing "data/applications/" rule, holding no
secrets -- only query fingerprints, counters, and timestamps. Mirrors
search_provider_usage_store.py's existing pattern (atomic write, advisory
only, never a distributed lock) rather than inventing a new one.

Two kinds of state are tracked here:

1. Query-level history, keyed by a deterministic fingerprint of
   (provider, source, role, location, max_job_age_days) -- used for
   Section 3 (query deduplication within a run) and Section 4
   (cooldown: skip a query that succeeded recently).

2. Source-level history (last_run_at, last_result_count,
   last_new_result_count) -- used for Section 5 (source-aware refresh
   intervals) independent of any single query fingerprint, since a
   source's own refresh gate should hold even if its query text
   changes run to run (e.g. consolidated vs. split-by-location).

This module makes no network call and never touches
data/applications/jobos.db.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY_STORE_PATH = ROOT / "data" / "applications" / "search_query_history.json"

_EMPTY_QUERY_RECORD = {
    "provider": None,
    "source": None,
    "role": None,
    "location": None,
    "query_text": None,
    "attempts": 0,
    "successful_runs": 0,
    "last_attempt_at": None,
    "last_success_at": None,
    "last_new_job_at": None,
    "result_count": 0,
    "new_result_count": 0,
    "total_results": 0,
    "total_new_results": 0,
    "average_results": 0.0,
    "average_new_results": 0.0,
    "error_state": None,
}

_EMPTY_SOURCE_RECORD = {
    "last_run_at": None,
    "last_result_count": 0,
    "last_new_result_count": 0,
    "last_error_state": None,
}


def _now():
    return datetime.now(timezone.utc)


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def load_history(path=None):
    # Resolved at CALL time (not bound as a function-definition-time
    # default) so a caller/test that reassigns the module-level
    # HISTORY_STORE_PATH attribute (the standard monkeypatch pattern
    # used elsewhere in this project, e.g. tests) actually takes
    # effect -- a `path=HISTORY_STORE_PATH` default would instead
    # freeze whatever HISTORY_STORE_PATH was at import time.
    path = path or HISTORY_STORE_PATH
    if not path.exists():
        return {"queries": {}, "sources": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"queries": {}, "sources": {}}
    data.setdefault("queries", {})
    data.setdefault("sources", {})
    return data


def save_history(data, path=None):
    _atomic_write(path or HISTORY_STORE_PATH, data)


def compute_query_fingerprint(provider, source, role, location, max_job_age_days):
    """Deterministic fingerprint of exactly what a query would ask for
    -- mirrors search_submission._fingerprint()'s pattern. Two entries
    that would produce the identical request (same provider, source,
    role, location string, freshness window) always fingerprint the
    same, whether they came from the same run or two different days."""
    payload = json.dumps(
        {
            "provider": (provider or "").lower(),
            "source": (source or "").upper(),
            "role": (role or "").strip().lower(),
            "location": (location or "").strip().lower(),
            "max_job_age_days": max_job_age_days,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def get_query_record(fingerprint, path=None):
    data = load_history(path)
    return dict(_EMPTY_QUERY_RECORD, **data["queries"].get(fingerprint, {}))


def is_query_in_cooldown(fingerprint, cooldown_hours, path=None):
    """True if this exact query fingerprint last SUCCEEDED within the
    cooldown window (a provider/source error never starts a cooldown --
    Section 4's "previous result was a provider error" bypass)."""
    if cooldown_hours is None or cooldown_hours <= 0:
        return False
    record = get_query_record(fingerprint, path)
    if not record["last_success_at"]:
        return False
    if record.get("error_state"):
        return False
    last_success = datetime.fromisoformat(record["last_success_at"])
    age_hours = (_now() - last_success).total_seconds() / 3600.0
    return age_hours < cooldown_hours


def record_query_result(
    fingerprint,
    *,
    provider,
    source,
    role,
    location,
    query_text,
    result_count,
    new_result_count,
    error_state=None,
    path=None,
):
    """Records the outcome of one executed query. error_state=None means
    the query executed without a provider/adapter-level error (an
    empty-but-successful result is NOT an error -- Section 1's "empty
    result = valid provider response" rule)."""
    data = load_history(path)
    record = dict(_EMPTY_QUERY_RECORD, **data["queries"].get(fingerprint, {}))
    now_iso = _now().isoformat(timespec="seconds")

    record.update(
        provider=provider,
        source=source,
        role=role,
        location=location,
        query_text=query_text,
    )
    record["attempts"] += 1
    record["last_attempt_at"] = now_iso
    record["error_state"] = error_state

    if error_state is None:
        record["successful_runs"] += 1
        record["last_success_at"] = now_iso
        record["result_count"] = result_count
        record["new_result_count"] = new_result_count
        record["total_results"] += result_count
        record["total_new_results"] += new_result_count
        if record["successful_runs"] > 0:
            record["average_results"] = record["total_results"] / record["successful_runs"]
            record["average_new_results"] = record["total_new_results"] / record["successful_runs"]
        if new_result_count > 0:
            record["last_new_job_at"] = now_iso

    data["queries"][fingerprint] = record
    save_history(data, path)
    return record


def get_source_record(source, path=None):
    data = load_history(path)
    return dict(_EMPTY_SOURCE_RECORD, **data["sources"].get(source, {}))


def is_source_due(source, refresh_interval_hours, path=None):
    """True if `source` has never run, or its last successful run is
    older than refresh_interval_hours. A source is never permanently
    suppressed: once its interval elapses it becomes eligible again."""
    if refresh_interval_hours is None or refresh_interval_hours <= 0:
        return True
    record = get_source_record(source, path)
    if not record["last_run_at"]:
        return True
    last_run = datetime.fromisoformat(record["last_run_at"])
    age_hours = (_now() - last_run).total_seconds() / 3600.0
    return age_hours >= refresh_interval_hours


def record_source_run(source, result_count, new_result_count, error_state=None, path=None):
    data = load_history(path)
    record = dict(_EMPTY_SOURCE_RECORD, **data["sources"].get(source, {}))
    record["last_run_at"] = _now().isoformat(timespec="seconds")
    record["last_result_count"] = result_count
    record["last_new_result_count"] = new_result_count
    record["last_error_state"] = error_state
    data["sources"][source] = record
    save_history(data, path)
    return record


def query_priority_score(fingerprint, source_due_score=0, path=None):
    """Simple, explainable heuristic (Section 10) -- NOT machine
    learning. Higher is better.

        priority = new_results_last_run
                 + freshness_success_bonus  (1 if last attempt succeeded, else 0)
                 - recently_run_penalty     (2 if attempted in the last 24h)

    source_due_score is passed in by the caller (Section 5's refresh
    gate already establishes whether the source is eligible at all --
    this only breaks ties/ranks among eligible candidates)."""
    record = get_query_record(fingerprint, path)
    new_results_last_run = record["new_result_count"] or 0
    freshness_success_bonus = 1 if (record["last_success_at"] and not record["error_state"]) else 0

    recently_run_penalty = 0
    if record["last_attempt_at"]:
        last_attempt = datetime.fromisoformat(record["last_attempt_at"])
        if (_now() - last_attempt).total_seconds() / 3600.0 < 24:
            recently_run_penalty = 2

    return new_results_last_run + freshness_success_bonus + source_due_score - recently_run_penalty
