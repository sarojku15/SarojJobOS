#!/usr/bin/env python3

"""
Ashby job-board adapter -- real implementation against Ashby's public,
unauthenticated job-board posting API
(https://api.ashbyhq.com/posting-api/job-board/{board_token}), kept
AdapterStatus.NOT_ENABLED pending the same phased, evidence-based
live-validation process every other source in this project goes
through. See greenhouse_adapter.py's module docstring for the shared
rationale.
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

API_BASE = "https://api.ashbyhq.com/posting-api/job-board"


def fetch_board_jobs(board_token):
    url = f"{API_BASE}/{board_token}"
    data = _fetch_json(url)
    return data.get("jobs", []) if isinstance(data, dict) else []


def normalize_ashby_job(board_token, raw_job):
    return {
        "source": "ASHBY",
        "company": board_token,
        "title": str(raw_job.get("title") or "").strip(),
        "location": str(raw_job.get("location") or "").strip(),
        "work_model": str(raw_job.get("employmentType") or "").strip(),
        "job_url": str(raw_job.get("jobUrl") or raw_job.get("applyUrl") or "").strip(),
        "application_url": str(raw_job.get("applyUrl") or raw_job.get("jobUrl") or "").strip(),
        "posted_date": str(raw_job.get("publishedAt") or "").strip(),
        "jd_text": str(raw_job.get("descriptionPlain") or "").strip(),
        "experience_required": "",
        "mandatory_skills": [],
        "preferred_skills": [],
    }


class AshbyAdapter(JobSourceAdapter):
    name = "ASHBY"
    status = AdapterStatus.NOT_ENABLED
    capabilities = frozenset()

    def health_check(self):
        boards = load_boards("ashby")
        if not boards:
            return AdapterHealth(
                source=self.name,
                reachable=False,
                block_reason=BlockReason.NONE,
                detail="No boards configured in config/career_pages.json -- nothing to check.",
            )
        try:
            _fetch_json(f"{API_BASE}/{boards[0]}")
            return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)
        except AtsFetchError as error:
            return AdapterHealth(
                source=self.name, reachable=False, block_reason=BlockReason.NETWORK_ERROR, detail=str(error)
            )

    def search(self, query: SearchQuery):
        boards = load_boards("ashby")
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
                job = normalize_ashby_job(board_token, raw_job)
                if query.location and job["location"] and query.location.lower() not in job["location"].lower():
                    continue
                results.append(job)
        return results
