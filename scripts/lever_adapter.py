#!/usr/bin/env python3

"""
Lever job-board adapter -- real implementation against Lever's public,
unauthenticated postings API (https://api.lever.co/v0/postings/{board_token}),
kept AdapterStatus.NOT_ENABLED pending the same phased, evidence-based
live-validation process every other source in this project goes
through. See greenhouse_adapter.py's module docstring for the shared
rationale (public documented API, no bypass involved, still gated
behind this project's own evidence discipline; no board configured by
default; zero network calls while NOT_ENABLED).
"""

from ats_common import AtsFetchError, _fetch_json, load_boards, role_matches
from source_adapter import (
    AdapterHealth,
    AdapterStatus,
    AdapterTimeoutError,
    BlockReason,
    JobSourceAdapter,
    SearchQuery,
)

API_BASE = "https://api.lever.co/v0/postings"


def fetch_board_jobs(board_token):
    url = f"{API_BASE}/{board_token}?mode=json"
    data = _fetch_json(url)
    return data if isinstance(data, list) else []


def normalize_lever_job(board_token, raw_job):
    categories = raw_job.get("categories") or {}
    location = str(categories.get("location") or "").strip()
    return {
        "source": "LEVER",
        "company": board_token,
        "title": str(raw_job.get("text") or "").strip(),
        "location": location,
        "work_model": str(categories.get("commitment") or "").strip(),
        "job_url": str(raw_job.get("hostedUrl") or "").strip(),
        "application_url": str(raw_job.get("applyUrl") or raw_job.get("hostedUrl") or "").strip(),
        "posted_date": str(raw_job.get("createdAt") or "").strip(),
        "jd_text": str((raw_job.get("descriptionPlain") or raw_job.get("description") or "")).strip(),
        "experience_required": "",
        "mandatory_skills": [],
        "preferred_skills": [],
    }


class LeverAdapter(JobSourceAdapter):
    name = "LEVER"
    status = AdapterStatus.NOT_ENABLED
    capabilities = frozenset()

    def health_check(self):
        boards = load_boards("lever")
        if not boards:
            return AdapterHealth(
                source=self.name,
                reachable=False,
                block_reason=BlockReason.NONE,
                detail="No boards configured in config/career_pages.json -- nothing to check.",
            )
        try:
            _fetch_json(f"{API_BASE}/{boards[0]}?mode=json")
            return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)
        except AtsFetchError as error:
            return AdapterHealth(
                source=self.name, reachable=False, block_reason=BlockReason.NETWORK_ERROR, detail=str(error)
            )

    def search(self, query: SearchQuery):
        boards = load_boards("lever")
        results = []
        for board_token in boards:
            try:
                raw_jobs = fetch_board_jobs(board_token)
            except AtsFetchError as error:
                raise AdapterTimeoutError(self.name, detail=str(error)) from error

            for raw_job in raw_jobs:
                title = str(raw_job.get("text") or "")
                if not role_matches(title, query.role):
                    continue
                job = normalize_lever_job(board_token, raw_job)
                if query.location and job["location"] and query.location.lower() not in job["location"].lower():
                    continue
                results.append(job)
        return results
