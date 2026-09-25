#!/usr/bin/env python3

import json
import sys
from pathlib import Path

from source_adapter import SearchQuery


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config/searches.json"


def load_search_config():
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Search configuration not found: {CONFIG_PATH}"
        )

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = json.load(f)

    required = [
        "locations",
        "roles",
        "sources",
        "experience_years",
        "minimum_score",
        "exclude_keywords",
    ]

    missing = [
        key for key in required
        if key not in config
    ]

    if missing:
        raise ValueError(
            "Missing required search configuration fields: "
            + ", ".join(missing)
        )

    return config


def build_queries(config):
    queries = []

    for source in config["sources"]:
        for role in config["roles"]:
            for location in config["locations"]:
                search_query = SearchQuery(
                    role=role,
                    location=location,
                    experience_years=config["experience_years"],
                    exclude_keywords=config["exclude_keywords"],
                    extra={
                        "minimum_score": config["minimum_score"],
                    },
                )

                queries.append(
                    {
                        "source": source,
                        "role": role,
                        "location": location,
                        "experience_years": config[
                            "experience_years"
                        ],
                        "minimum_score": config[
                            "minimum_score"
                        ],
                        "exclude_keywords": config[
                            "exclude_keywords"
                        ],
                        "search_query": search_query,
                    }
                )

    return queries


def main():
    config = load_search_config()
    queries = build_queries(config)

    print("SEARCH QUERY PLANNER")
    print("====================")

    print(f"Sources       : {len(config['sources'])}")
    print(f"Roles         : {len(config['roles'])}")
    print(f"Locations     : {len(config['locations'])}")
    print(
        f"Total queries : {len(queries)}"
    )
    print(
        f"Min score     : {config['minimum_score']}"
    )
    print(
        f"Experience    : {config['experience_years']} years"
    )

    print()
    print("First 10 queries")
    print("================")

    for query in queries[:10]:
        print(
            f"{query['source']} | "
            f"{query['role']} | "
            f"{query['location']} | "
            f"min_score={query['minimum_score']}"
        )

    print()

    expected_count = (
        len(config["sources"])
        * len(config["roles"])
        * len(config["locations"])
    )

    if len(queries) != expected_count:
        print(
            "FAIL: query count does not match "
            "source × role × location matrix."
        )
        sys.exit(1)

    print(
        "PASS: search configuration expanded "
        "into a complete query matrix."
    )


# ---------------------------------------------------------------------
# Candidate-search-profile-driven query generation.
#
# Distinct from build_queries(config) above, which expands the whole,
# Saroj-specific config/searches.json into one large fixed matrix.
# build_queries_from_search_profile() instead derives a deterministic,
# CAPPED query plan from any candidate's search_profile.SearchProfile
# (itself built from that candidate's own CONFIRMED CandidateProfile) --
# no config file is read here, and no candidate's roles/locations are
# ever hardcoded.
# ---------------------------------------------------------------------

DEFAULT_MAX_QUERIES_PER_SUBMISSION = 50


def build_queries_from_search_profile(
    search_profile, max_queries=DEFAULT_MAX_QUERIES_PER_SUBMISSION
):
    """
    Deterministically derive a capped list of query-plan entries from a
    search_profile.SearchProfile: sources x roles x locations, each
    dimension sorted first for determinism, deduplicated, and capped at
    `max_queries` so a candidate with many roles/locations does not
    blindly explode into an enormous matrix.

    Returns a list of dicts: {"sequence", "source", "role", "location",
    "max_job_age_days"}, in stable, reproducible order across repeated
    calls with the same input -- the same search profile always yields
    the same plan.

    max_job_age_days (a discovery-level freshness constraint -- see
    search_profile.build_search_profile()'s docstring) is copied onto
    EVERY entry, not stored once at the plan level: each entry is
    already a fully self-describing query dict search_worker.py
    reconstructs a SearchQuery from independently, and this keeps that
    reconstruction correct without teaching it a second, plan-level
    shape to look at.

    Does not call SearchQuery(...) here (unlike build_queries() above)
    -- these plain dicts are the search-run/search-queue persistence
    shape (see search_submission.py); a future worker consuming
    search_queue is expected to construct a real
    source_adapter.SearchQuery from each entry only at execution time,
    which this function -- queuing only, never executing -- does not do.
    """
    sources = sorted(set(search_profile.sources))
    roles = sorted(set(search_profile.target_roles))
    locations = sorted(set(search_profile.target_locations))

    plan = []
    seen = set()

    for source in sources:
        for role in roles:
            for location in locations:
                key = (source, role, location)

                if key in seen:
                    continue

                seen.add(key)
                plan.append(
                    {
                        "sequence": len(plan),
                        "source": source,
                        "role": role,
                        "location": location,
                        "max_job_age_days": search_profile.max_job_age_days,
                    }
                )

                if len(plan) >= max_queries:
                    return plan

    return plan


if __name__ == "__main__":
    main()
