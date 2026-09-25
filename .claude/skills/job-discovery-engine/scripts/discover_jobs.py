#!/usr/bin/env python3

"""
Main orchestrator for the job-discovery-engine Skill (see ../SKILL.md).

discover(criteria) is the one public entry point: generic
SearchCriteria in -> DiscoveryOutcome out (see ../references/job-schema.md
for both shapes). It builds a bounded query plan
(../references/search-query-strategy.md), dispatches through the
EXISTING source_registry.discover_from_sources() for every currently
enabled source, applies quality validation and normalization, and
returns. It does not score, deduplicate cross-source, classify
freshness, or write to any database -- see SKILL.md's scope statement.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS_DIR = ROOT / "scripts"
SKILL_SCRIPTS_DIR = Path(__file__).resolve().parent
for p in (SCRIPTS_DIR, SKILL_SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import source_capabilities
import source_registry
from source_adapter import AdapterBlockedError, AdapterNotEnabledError, AdapterTimeoutError, SearchQuery

from validate_discovered_job import validate_batch
from normalize_discovered_jobs import dedup_within_batch, normalize_discovered_jobs

MAX_ADAPTER_QUERIES = 50  # matches query_planner.DEFAULT_MAX_QUERIES_PER_SUBMISSION
MAX_WEB_SEARCH_QUERIES_PER_PAIR = 8


@dataclass
class DiscoveryOutcome:
    jobs: list = field(default_factory=list)
    queries_run: list = field(default_factory=list)
    sources_used: list = field(default_factory=list)
    unavailable_sources: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def as_dict(self):
        return {
            "jobs": self.jobs,
            "queries_run": self.queries_run,
            "sources_used": self.sources_used,
            "unavailable_sources": self.unavailable_sources,
            "rejected": self.rejected,
            "errors": self.errors,
        }


def _resolve_sources(requested_sources):
    """Split a candidate's requested source list into (usable, unavailable),
    reading live status from source_capabilities.py -- never a hardcoded
    list. requested_sources=None means "every currently enabled,
    non-MOCK source."""
    all_caps = {c["source_name"]: c for c in source_capabilities.list_source_capabilities()}

    if requested_sources is None:
        usable = [name for name, c in all_caps.items() if c["enabled"] and name != "MOCK"]
        return usable, []

    usable = []
    unavailable = []
    for name in requested_sources:
        name = name.upper()
        cap = all_caps.get(name)
        if cap is None:
            unavailable.append({"source_name": name, "reason": "UNKNOWN_SOURCE -- not registered"})
        elif not cap["enabled"]:
            unavailable.append({"source_name": name, "reason": cap["reason"]})
        else:
            usable.append(name)

    return usable, unavailable


def build_adapter_query_plan(criteria, sources):
    """One SearchQuery per (title x location) pair per source, capped at
    MAX_ADAPTER_QUERIES total -- mirrors query_planner.py's existing
    bounded cross-product, kept separate here so this Skill can plan
    from a plain criteria dict without constructing a full
    CandidateProfile/SearchProfile."""
    titles = criteria.get("titles") or []
    locations = criteria.get("locations") or []
    experience_years = criteria.get("experience_min")
    max_job_age_days = criteria.get("max_job_age_days")

    plan = []
    for source in sources:
        for title, location in product(titles, locations):
            if len(plan) >= MAX_ADAPTER_QUERIES:
                return plan
            plan.append(
                (
                    source,
                    SearchQuery(
                        role=title,
                        location=location,
                        experience_years=experience_years,
                        max_job_age_days=max_job_age_days,
                    ),
                )
            )
    return plan


def build_web_search_query_strings(criteria):
    """Bounded, deterministic web-search phrasings -- see
    ../references/search-query-strategy.md. Every token comes directly
    from `criteria`; nothing is invented."""
    titles = criteria.get("titles") or []
    locations = criteria.get("locations") or []
    skills = criteria.get("skills") or []
    work_models = [w.upper() for w in (criteria.get("work_model") or [])]
    exp_min = criteria.get("experience_min")
    exp_max = criteria.get("experience_max")

    queries = []
    for title, location in product(titles[:1] or [""], locations):
        combo = []
        combo.append(f'"{title}" {location}'.strip())
        if skills:
            combo.append(f'"{title}" {location} ' + " ".join(skills[:2]))
        if "REMOTE" in work_models:
            combo.append(f'"{title}" remote')
        if exp_min is not None and exp_max is not None:
            combo.append(f'"{title}" {location} {exp_min}-{exp_max} years')
        for alt_title in titles[1:3]:
            combo.append(f'"{alt_title}" {location}')

        queries.extend(combo[:MAX_WEB_SEARCH_QUERIES_PER_PAIR])

    return queries[: MAX_WEB_SEARCH_QUERIES_PER_PAIR * max(len(locations), 1)]


def discover(criteria):
    """The one public entry point. See module docstring."""
    outcome = DiscoveryOutcome()

    usable_sources, unavailable = _resolve_sources(criteria.get("sources"))
    outcome.unavailable_sources = unavailable

    if not usable_sources:
        return outcome

    query_plan = build_adapter_query_plan(criteria, usable_sources)
    outcome.queries_run = [
        {"source": source, "role": q.role, "location": q.location} for source, q in query_plan
    ]
    outcome.sources_used = sorted({source for source, _ in query_plan})

    if not query_plan:
        return outcome

    try:
        raw_jobs = source_registry.discover_from_sources(query_plan)
    except (AdapterBlockedError, AdapterTimeoutError, AdapterNotEnabledError) as error:
        outcome.errors.append({"source": getattr(error, "source", "UNKNOWN"), "detail": str(error)})
        raw_jobs = []
    except ValueError as error:
        # Unknown/unimplemented source name -- surfaced, never swallowed.
        outcome.errors.append({"source": "UNKNOWN", "detail": str(error)})
        raw_jobs = []

    deduped = dedup_within_batch(raw_jobs)
    accepted, rejected = validate_batch(deduped)
    outcome.rejected = rejected

    normalized, normalize_errors = normalize_discovered_jobs(accepted)
    for e in normalize_errors:
        outcome.errors.append({"source": e["job"].get("source", "UNKNOWN"), "detail": e["error"]})

    outcome.jobs = normalized
    return outcome


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run the job-discovery-engine Skill against a criteria JSON blob")
    parser.add_argument("--criteria-json", required=True, help="JSON-encoded SearchCriteria object")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    criteria = json.loads(args.criteria_json)
    outcome = discover(criteria)
    print(json.dumps(outcome.as_dict(), indent=2, default=str))


if __name__ == "__main__":
    main()
