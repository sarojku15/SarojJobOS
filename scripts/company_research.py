#!/usr/bin/env python3
"""
Phase 2: company research persistence.

Reuses the EXISTING, already-configured, already-tested multi-provider
search layer (scripts/search_provider_manager.py -- You/Tavily/Exa/
Brave/Serper, real keys already in this project's own .env) -- never a
second search implementation, never a live-LLM call (none is wired
into this backend; see docs/ARCHITECTURE.md).

Honesty rule, enforced by construction: every field this module
populates (website, description, recent_info) is either a real
provider-returned URL or a verbatim snippet from a real provider
result, and every populated field's row also has at least one entry in
source_urls_json. Fields this module cannot reliably and safely infer
without an LLM (industry, headquarters, company_size, tech_indicators,
role_context, hiring_signals) are left null rather than guessed --
never fabricated to look complete. `status` is always one of:

  NOT_ATTEMPTED -- no search-provider API key configured at all
  FAILED        -- a search was attempted but every provider failed
  PARTIAL       -- a search succeeded but no confident website/
                   description could be extracted from the results
  SUCCESS       -- a search succeeded and a website and/or description
                   were extracted, each with a real source URL

Never blocks job search -- this module is only ever called on
explicit user/Claude request, from a route that catches its own
exceptions.
"""
import json
import re
import sqlite3
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from search_provider_manager import get_manager, SearchProviderPoolExhausted
import search_provider as sp


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _domain(url):
    match = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    return match.group(1).lower() if match else None


def _looks_like_official_site(url, company_name):
    domain = _domain(url)
    if not domain:
        return False
    company_tokens = re.findall(r"[a-z0-9]+", company_name.lower())
    if not company_tokens:
        return False
    # Conservative: the company's own (first, most distinctive) name
    # token must appear in the domain, and the domain must not be an
    # obvious social/aggregator/job-board host -- a real, if simple,
    # heuristic, never a guess dressed up as certainty.
    excluded_hosts = (
        "linkedin.com", "glassdoor.com", "indeed.com", "naukri.com",
        "wikipedia.org", "crunchbase.com", "twitter.com", "x.com",
        "facebook.com", "youtube.com", "instagram.com",
    )
    if any(domain.endswith(host) for host in excluded_hosts):
        return False
    return company_tokens[0] in domain.replace("-", "")


def has_any_provider_configured():
    manager = get_manager()
    return bool(manager.eligible_providers())


def research_company(conn, candidate_id, company_name, job_id=None):
    """
    Runs a real search for `company_name`, conservatively extracts a
    website + description (only if a result plausibly matches the
    official site), and persists a NEW company_research row (never
    overwrites an earlier version -- see migrate_v12_company_research.py).
    Returns the persisted record as a dict.
    """
    manager = get_manager()
    if not manager.eligible_providers():
        return _persist(
            conn, candidate_id, company_name, job_id, status="NOT_ATTEMPTED",
            error_message="No search-provider API key configured (see docs/CONFIGURATION.md).",
        )

    query = f'"{company_name}" company official website about'
    try:
        result = manager.search(query, num=8)
    except SearchProviderPoolExhausted as error:
        return _persist(
            conn, candidate_id, company_name, job_id, status="FAILED",
            error_message=f"Every configured search provider failed: {error.args[0]!r}",
        )

    website = None
    description = None
    source_urls = []
    recent_info = []
    for r in result.results:
        url = r.get("url") or ""
        snippet = r.get("snippet") or ""
        if not url:
            continue
        source_urls.append(url)
        if website is None and _looks_like_official_site(url, company_name):
            website = url
            if snippet:
                description = snippet
        elif snippet:
            recent_info.append({"snippet": snippet, "url": url, "title": r.get("title"), "date": r.get("date")})

    status = "SUCCESS" if (website or description) else "PARTIAL"
    return _persist(
        conn, candidate_id, company_name, job_id, status=status,
        website=website, description=description,
        recent_info=recent_info[:5], source_urls=source_urls,
        provider_used=result.provider_name,
    )


def _persist(conn, candidate_id, company_name, job_id, status, website=None,
             description=None, recent_info=None, source_urls=None,
             error_message=None, provider_used=None):
    next_version = 1 + (
        conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM company_research WHERE candidate_id = ? AND company_name = ?",
            (candidate_id, company_name),
        ).fetchone()[0]
    )
    company_research_id = f"research_{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO company_research (
            company_research_id, candidate_id, job_id, company_name, version,
            status, website, description, industry, headquarters, company_size,
            tech_indicators_json, role_context, recent_info_json, hiring_signals_json,
            source_urls_json, error_message, researched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, NULL, ?, ?, ?)
        """,
        (
            company_research_id, candidate_id, job_id, company_name, next_version,
            status, website, description,
            json.dumps(recent_info or []),
            json.dumps(source_urls or []),
            error_message, _now(),
        ),
    )
    conn.commit()
    return get_company_research(conn, candidate_id, company_research_id)


def get_company_research(conn, candidate_id, company_research_id):
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM company_research WHERE company_research_id = ? AND candidate_id = ?",
        (company_research_id, candidate_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown company research record: {company_research_id!r}")
    return _row_to_dict(row)


def list_company_research(conn, candidate_id, company_name=None):
    conn.row_factory = sqlite3.Row
    if company_name is not None:
        rows = conn.execute(
            "SELECT * FROM company_research WHERE candidate_id = ? AND company_name = ? ORDER BY version DESC",
            (candidate_id, company_name),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM company_research WHERE candidate_id = ? ORDER BY researched_at DESC",
            (candidate_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row):
    d = dict(row)
    for field in ("tech_indicators_json", "recent_info_json", "hiring_signals_json", "source_urls_json"):
        key = field[: -len("_json")]
        d[key] = json.loads(d.pop(field) or "null")
    return d
