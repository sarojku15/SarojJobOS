#!/usr/bin/env python3

"""
Shared helpers for the generic ATS/career-page provider family
(greenhouse_adapter.py / lever_adapter.py / ashby_adapter.py). Each
platform's public job-board API returns a different JSON shape;
_fetch_json() is the one network primitive all three reuse, and
_role_matches()/_load_boards() are the one keyword-filter and
config-loading implementation all three reuse -- no per-platform
duplicate of either.

These are documented, public, unauthenticated JSON endpoints
(Greenhouse's boards-api, Lever's api.lever.co, Ashby's
posting-api) -- reading them is not a CAPTCHA/robots/anti-bot bypass;
no login wall or rate-limit evasion is involved. They are still kept
AdapterStatus.NOT_ENABLED (see each adapter module) pending the same
phased, evidence-based live-validation process every other source in
this project goes through before being marked ENABLED -- this module
performs no live call unless an adapter's search()/health_check()
explicitly invokes it, and no adapter does that while NOT_ENABLED
(source_registry.discover_from_sources() skips it first).
"""

import json
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAREER_PAGES_CONFIG = ROOT / "config" / "career_pages.json"

DEFAULT_TIMEOUT_SECONDS = 10
USER_AGENT = "SarojJobOS/1.0 (+career-page discovery; contact: local dev)"


class AtsFetchError(Exception):
    """Raised for any network/parse failure talking to an ATS's public
    API -- callers translate this into the project's existing
    AdapterBlockedError/AdapterTimeoutError vocabulary, never swallow
    it silently."""


def _fetch_json(url, timeout=DEFAULT_TIMEOUT_SECONDS):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AtsFetchError(f"GET {url} failed: {error}") from error

    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise AtsFetchError(f"GET {url} returned non-JSON body: {error}") from error


def load_boards(platform):
    """Read config/career_pages.json's "boards" list, filtered to the
    given platform ("greenhouse"/"lever"/"ashby"). Returns a list of
    board_token strings. Empty list (never a fabricated example) when
    the config file is missing, empty, or has no entries for this
    platform -- this project ships zero real company board tokens by
    default."""
    if not CAREER_PAGES_CONFIG.exists():
        return []

    try:
        data = json.loads(CAREER_PAGES_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    boards = data.get("boards") or []
    return [
        b["board_token"]
        for b in boards
        if isinstance(b, dict) and b.get("platform") == platform and b.get("board_token")
    ]


def role_matches(job_title, query_role):
    """Simple, honest substring/word-overlap match -- never a fabricated
    "this matches" signal. A career-page board has no free-text search
    endpoint the way a job board's search page does, so this is the
    filtering step standing in for one: every word in query_role must
    appear in job_title (case-insensitive), OR job_title appears
    verbatim in query_role. No fuzzy/semantic matching is attempted."""
    if not job_title or not query_role:
        return False

    title_lower = job_title.lower()
    role_lower = query_role.lower()

    if role_lower in title_lower or title_lower in role_lower:
        return True

    role_words = [w for w in role_lower.split() if len(w) > 2]
    if not role_words:
        return False

    return all(w in title_lower for w in role_words)
