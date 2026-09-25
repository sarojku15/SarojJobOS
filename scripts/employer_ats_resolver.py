#!/usr/bin/env python3

"""
Employer/ATS fallback detection (Phase 13 broad-discovery, Part 12).

Pure, offline, no-network URL pattern matching: given a job's
application_url (or job_url), detect whether it points at one of this
project's three already-implemented ATS platforms (Greenhouse, Lever,
Ashby -- see greenhouse_adapter.py/lever_adapter.py/ashby_adapter.py)
and, if so, extract that platform's board token.

This module never fetches a URL and never decides to enable an
adapter. It only answers "if JobOS wanted to follow this job to its
employer's own career page, which existing adapter (if any) already
knows how to talk to that page, and is a board token for it already
configured in config/career_pages.json?" Actually retrieving jobs from
that board still goes through GreenhouseAdapter/LeverAdapter/
AshbyAdapter exactly as before -- both remain AdapterStatus.NOT_ENABLED
until they individually pass this project's phased, evidence-based
live-validation process. Detecting a match here does not change that.
"""

import re

# Longest-lived, most stable public URL shapes for each platform.
# job-boards.greenhouse.io is Greenhouse's newer domain; boards.greenhouse.io
# is the older one -- both are matched.
_PATTERNS = {
    "greenhouse": re.compile(
        r"(?:boards|job-boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)", re.IGNORECASE
    ),
    "lever": re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)", re.IGNORECASE),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)", re.IGNORECASE),
}


def detect_ats_platform(url):
    """Returns (platform, board_token) for a recognized ATS URL, or
    (None, None) if the URL does not match any known ATS pattern (the
    overwhelmingly common case -- most job postings do not link to one
    of these three platforms). Never guesses a partial match."""
    url = str(url or "")
    for platform, pattern in _PATTERNS.items():
        match = pattern.search(url)
        if match:
            return platform, match.group(1)
    return None, None


def resolve_ats_fallback(application_url, job_url=""):
    """Convenience wrapper: tries application_url first (the more
    likely place an employer's own ATS link would appear), then
    job_url. Returns a dict describing what was found -- never raises,
    never fetches anything."""
    for candidate_url in (application_url, job_url):
        platform, board_token = detect_ats_platform(candidate_url)
        if platform:
            configured = is_board_configured(platform, board_token)
            return {
                "detected": True,
                "platform": platform,
                "board_token": board_token,
                "matched_url": candidate_url,
                "board_configured": configured,
                "note": (
                    f"This job's application URL points at a {platform.capitalize()} board "
                    f"({board_token!r}). "
                    + (
                        "That board is already configured in config/career_pages.json."
                        if configured
                        else "That board is NOT yet configured in config/career_pages.json -- "
                        "add it there and complete this project's phased live-validation "
                        f"process before {platform.capitalize()}Adapter can be enabled for it."
                    )
                ),
            }
    return {
        "detected": False,
        "platform": None,
        "board_token": None,
        "matched_url": None,
        "board_configured": False,
        "note": "No recognized ATS URL pattern (Greenhouse/Lever/Ashby) found on this job.",
    }


def is_board_configured(platform, board_token):
    import ats_common

    return board_token in ats_common.load_boards(platform)


if __name__ == "__main__":
    import json
    import sys

    for test_url in sys.argv[1:] or [
        "https://boards.greenhouse.io/examplecompany/jobs/1234567",
        "https://jobs.lever.co/examplecompany/abcd-1234",
        "https://jobs.ashbyhq.com/examplecompany/posting-abc",
        "https://www.linkedin.com/jobs/view/1234567890/",
    ]:
        print(test_url, "->", json.dumps(resolve_ats_fallback(test_url)))
