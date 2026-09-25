#!/usr/bin/env python3

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from query_planner import load_search_config, build_queries
from source_registry import discover_from_sources


def main():
    config = load_search_config()
    queries = build_queries(config)

    # Intentionally test only two queries.
    test_queries = [
        query
        for query in queries
        if query["source"] == "LinkedIn"
    ][:2]

    print("QUERY → REGISTRY ORCHESTRATOR TEST")
    print("===================================")

    print(
        f"Total planned queries : {len(queries)}"
    )

    print(
        f"Test queries executed : {len(test_queries)}"
    )

    for query in test_queries:
        print(
            f"  {query['source']} | "
            f"{query['role']} | "
            f"{query['location']}"
        )

    # LinkedIn is not a real adapter yet.
    # For this local orchestration test, map the two
    # queries to MOCK while preserving the original
    # source metadata for validation.
    mock_queries = []

    for query in test_queries:
        mock_query = dict(query)
        mock_query["source"] = "MOCK"
        mock_queries.append(mock_query)

    jobs = discover_from_sources(mock_queries)

    print()
    print(
        f"Jobs returned by registry : {len(jobs)}"
    )

    for job in jobs:
        print(
            f"  {job['source']} | "
            f"{job['company']} | "
            f"{job['title']} | "
            f"{job['location']}"
        )

    expected = (
        len(test_queries) == 2
        and len(jobs) == 2
        and all(
            job["source"] == "MOCK"
            for job in jobs
        )
    )

    print()

    if expected:
        print(
            "PASS: query planner output successfully "
            "reached the source registry."
        )
    else:
        print(
            "FAIL: query orchestration regression detected."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
