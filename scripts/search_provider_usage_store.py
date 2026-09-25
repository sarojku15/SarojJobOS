#!/usr/bin/env python3

"""
Local, advisory usage tracking for the search-provider pool (Phase
14.2, Part 6).

Deliberately NOT the production job/application data model: this reads
and writes ONE small JSON file
(data/applications/search_provider_usage.json), never
data/applications/jobos.db, never any candidate/job/candidate_job_matches
table. It is git-ignored (see .gitignore's existing "data/applications/"
rule) and holds no secrets -- only counters and timestamps.

Every counter here is an ADVISORY LOCAL BUDGET, never an assertion of a
provider's actual remaining quota: this project has no way to query
any of these providers' real account-level quota. QUOTA_EXHAUSTED is
set only when a provider's own API actually told us so (via
search_provider.ProviderErrorType.QUOTA_EXHAUSTED) or when a
configured local daily/monthly request-count ceiling
(SEARCH_PROVIDER_MAX_DAILY_REQUESTS / _MONTHLY, Part 6) has been
reached -- never guessed.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USAGE_STORE_PATH = ROOT / "data" / "applications" / "search_provider_usage.json"

_EMPTY_PROVIDER_ENTRY = {
    "request_count_daily": 0,
    "successful_count_daily": 0,
    "failure_count_daily": 0,
    "quota_failures_daily": 0,
    "window_start_daily": None,
    "request_count_monthly": 0,
    "successful_count_monthly": 0,
    "failure_count_monthly": 0,
    "quota_failures_monthly": 0,
    "window_start_monthly": None,
    "last_request_at": None,
    "last_success_at": None,
    "last_error_at": None,
    "last_error": None,
    "current_status": "AVAILABLE",  # AVAILABLE | QUOTA_EXHAUSTED | AUTH_FAILED | RATE_LIMITED | ERROR | DISABLED
}


def _now():
    return datetime.now(timezone.utc)


def _today_str(now):
    return now.date().isoformat()


def _month_str(now):
    return now.strftime("%Y-%m")


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def load_usage(path=USAGE_STORE_PATH):
    if not path.exists():
        return {"providers": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"providers": {}}
    if "providers" not in data:
        data["providers"] = {}
    return data


def save_usage(data, path=USAGE_STORE_PATH):
    _atomic_write(path, data)


def _roll_windows(entry, now):
    """Resets daily/monthly counters when the window has rolled over.
    Mutates `entry` in place; caller persists."""
    today = _today_str(now)
    if entry.get("window_start_daily") != today:
        entry["window_start_daily"] = today
        entry["request_count_daily"] = 0
        entry["successful_count_daily"] = 0
        entry["failure_count_daily"] = 0
        entry["quota_failures_daily"] = 0

    month = _month_str(now)
    if entry.get("window_start_monthly") != month:
        entry["window_start_monthly"] = month
        entry["request_count_monthly"] = 0
        entry["successful_count_monthly"] = 0
        entry["failure_count_monthly"] = 0
        entry["quota_failures_monthly"] = 0


def get_provider_usage(provider_name, path=USAGE_STORE_PATH):
    """Read-only view of one provider's usage entry, with windows
    rolled forward for DISPLAY purposes -- does not persist the roll
    (record_request() is what actually persists a roll, on the next
    real request)."""
    data = load_usage(path)
    entry = dict(_EMPTY_PROVIDER_ENTRY, **data["providers"].get(provider_name, {}))
    _roll_windows(entry, _now())
    return entry


def record_request(provider_name, outcome, error_type=None, error_detail=None, path=USAGE_STORE_PATH):
    """outcome: "success" | "failure". error_type: a
    search_provider.ProviderErrorType value (string) when outcome is
    "failure" -- used to set current_status precisely (QUOTA_EXHAUSTED/
    AUTH_FAILED/RATE_LIMITED/ERROR)."""
    data = load_usage(path)
    entry = dict(_EMPTY_PROVIDER_ENTRY, **data["providers"].get(provider_name, {}))
    now = _now()
    _roll_windows(entry, now)

    now_iso = now.isoformat(timespec="seconds")
    entry["request_count_daily"] += 1
    entry["request_count_monthly"] += 1
    entry["last_request_at"] = now_iso

    if outcome == "success":
        entry["successful_count_daily"] += 1
        entry["successful_count_monthly"] += 1
        entry["last_success_at"] = now_iso
        entry["current_status"] = "AVAILABLE"
        entry["last_error"] = None
    else:
        entry["failure_count_daily"] += 1
        entry["failure_count_monthly"] += 1
        entry["last_error_at"] = now_iso
        entry["last_error"] = error_detail or ""
        if error_type == "QUOTA_EXHAUSTED":
            entry["quota_failures_daily"] += 1
            entry["quota_failures_monthly"] += 1
            entry["current_status"] = "QUOTA_EXHAUSTED"
        elif error_type == "AUTH_FAILED":
            entry["current_status"] = "AUTH_FAILED"
        elif error_type == "RATE_LIMITED":
            entry["current_status"] = "RATE_LIMITED"
        else:
            entry["current_status"] = "ERROR"

    data["providers"][provider_name] = entry
    save_usage(data, path)
    return entry


def check_local_budget(provider_name, max_daily=None, max_monthly=None, path=USAGE_STORE_PATH):
    """True if this provider is still within its configured LOCAL
    advisory budget (Part 6) -- max_daily/max_monthly of None means "no
    local ceiling configured," never treated as zero. Does not check
    the provider's own actual quota (this project has no way to)."""
    entry = get_provider_usage(provider_name, path)
    if max_daily is not None and entry["request_count_daily"] >= max_daily:
        return False
    if max_monthly is not None and entry["request_count_monthly"] >= max_monthly:
        return False
    return True


def reset_provider_usage(provider_name, window=None, path=USAGE_STORE_PATH):
    """Manually reset a provider's local usage counters (Part 3's GUI
    "reset local usage counters" action). window: None resets both
    daily and monthly; "daily" or "monthly" resets only that window."""
    data = load_usage(path)
    entry = dict(_EMPTY_PROVIDER_ENTRY, **data["providers"].get(provider_name, {}))

    if window in (None, "daily"):
        entry["window_start_daily"] = None
        entry["request_count_daily"] = 0
        entry["successful_count_daily"] = 0
        entry["failure_count_daily"] = 0
        entry["quota_failures_daily"] = 0
    if window in (None, "monthly"):
        entry["window_start_monthly"] = None
        entry["request_count_monthly"] = 0
        entry["successful_count_monthly"] = 0
        entry["failure_count_monthly"] = 0
        entry["quota_failures_monthly"] = 0
    if window is None:
        entry["current_status"] = "AVAILABLE"
        entry["last_error"] = None

    data["providers"][provider_name] = entry
    save_usage(data, path)
    return entry
