#!/usr/bin/env python3

"""
Greenhouse job-board adapter -- a real, working implementation against
Greenhouse's public, unauthenticated job-board API
(https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs), kept
AdapterStatus.NOT_ENABLED pending the same phased, evidence-based
live-validation process (offline implementation + tests -> one live
query -> controlled multi-query) every other source in this project
goes through before being marked ENABLED -- see CLAUDE.md's "Source
Adapter Principle". No board token is configured by default (see
config/career_pages.json); this file makes ZERO network calls while
NOT_ENABLED (source_registry.discover_from_sources() skips it first),
and even once ENABLED it only ever reads whatever boards a human has
explicitly listed in that config file -- it never invents or guesses a
company board token.

Unlike Naukri (a keyword search engine), a Greenhouse board has no
free-text search endpoint -- this adapter lists every open job on each
configured board and filters client-side by ats_common.role_matches().
Location filtering is a plain substring match against Greenhouse's own
reported location string; nothing is inferred beyond what the API
returns.
"""

from ats_common import AtsFetchError, _fetch_json, load_boards, role_matches
from source_adapter import (
    AdapterBlockedError,
    AdapterHealth,
    AdapterStatus,
    AdapterTimeoutError,
    BlockReason,
    JobSourceAdapter,
    SearchQuery,
)

API_BASE = "https://boards-api.greenhouse.io/v1/boards"


def _job_url(board_token, job):
    return job.get("absolute_url") or f"https://boards.greenhouse.io/{board_token}/jobs/{job.get('id', '')}"


def _location_string(job):
    location = job.get("location") or {}
    if isinstance(location, dict):
        return str(location.get("name") or "").strip()
    return str(location or "").strip()


def fetch_board_jobs(board_token):
    """One board's full open-jobs list, Greenhouse's own raw JSON shape
    (a list of {"id", "title", "absolute_url", "location", ...})."""
    url = f"{API_BASE}/{board_token}/jobs?content=true"
    data = _fetch_json(url)
    return data.get("jobs", []) if isinstance(data, dict) else []


def normalize_greenhouse_job(board_token, raw_job):
    return {
        "source": "GREENHOUSE",
        "company": board_token,
        "title": str(raw_job.get("title") or "").strip(),
        "location": _location_string(raw_job),
        "work_model": "",
        "job_url": _job_url(board_token, raw_job),
        "application_url": _job_url(board_token, raw_job),
        "posted_date": str(raw_job.get("updated_at") or "").strip(),
        "jd_text": str(raw_job.get("content") or "").strip(),
        "experience_required": "",
        "mandatory_skills": [],
        "preferred_skills": [],
    }


class GreenhouseAdapter(JobSourceAdapter):
    name = "GREENHOUSE"
    status = AdapterStatus.NOT_ENABLED
    capabilities = frozenset()

    def health_check(self):
        boards = load_boards("greenhouse")
        if not boards:
            return AdapterHealth(
                source=self.name,
                reachable=False,
                block_reason=BlockReason.NONE,
                detail="No boards configured in config/career_pages.json -- nothing to check.",
            )
        try:
            _fetch_json(f"{API_BASE}/{boards[0]}/jobs")
            return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)
        except AtsFetchError as error:
            return AdapterHealth(
                source=self.name, reachable=False, block_reason=BlockReason.NETWORK_ERROR, detail=str(error)
            )

    def search(self, query: SearchQuery):
        boards = load_boards("greenhouse")
        results = []
        for board_token in boards:
            try:
                raw_jobs = fetch_board_jobs(board_token)
            except AtsFetchError as error:
                raise AdapterTimeoutError(self.name, detail=str(error)) from error

            for raw_job in raw_jobs:
                title = str(raw_job.get("title") or "")
                if not role_matches(title, query.role):
                    continue
                job = normalize_greenhouse_job(board_token, raw_job)
                if query.location and query.location.lower() not in (job["location"] or "").lower():
                    if job["location"]:
                        continue
                results.append(job)
        return results
