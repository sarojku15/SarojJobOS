#!/usr/bin/env python3

"""
Offline job-search CLI/report: runs offline_search_pipeline.py's pure
in-memory pipeline against a JSON fixture and prints a human-readable
report (or, with --json, a machine-readable one).

Reads ONLY the given fixture file. No database connection, no source
adapter, no network access -- everything comes from the fixture.

Usage:
    python3 scripts/run_offline_search.py --fixture data/fixtures/ranking/ranking_dataset.json
    python3 scripts/run_offline_search.py --fixture data/fixtures/ranking/ranking_dataset.json --top 5
    python3 scripts/run_offline_search.py --fixture data/fixtures/ranking/ranking_dataset.json --json

Fixture shape expected: {"candidate_profile": {...}, "cases": [{"job": {...}}, ...]}
-- the same shape data/fixtures/ranking/ranking_dataset.json already uses.
"""

import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from offline_search_pipeline import run_offline_pipeline

DEFAULT_TOP_N = 10


def _parse_args(argv):
    fixture_path = None
    as_json = False
    top_n = DEFAULT_TOP_N

    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--fixture":
            if i + 1 >= len(argv):
                print("ERROR: --fixture requires a path")
                sys.exit(1)
            fixture_path = argv[i + 1]
            i += 2
        elif token == "--json":
            as_json = True
            i += 1
        elif token == "--top":
            if i + 1 >= len(argv):
                print("ERROR: --top requires a value")
                sys.exit(1)
            try:
                top_n = int(argv[i + 1])
            except ValueError:
                print(f"ERROR: --top must be an integer, got {argv[i + 1]!r}")
                sys.exit(1)
            i += 2
        else:
            print(f"ERROR: unrecognized argument: {token}")
            sys.exit(1)

    if fixture_path is None:
        print("ERROR: --fixture <path> is required")
        sys.exit(1)

    return fixture_path, as_json, top_n


def load_fixture(fixture_path):
    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    candidate_profile = data["candidate_profile"]
    raw_jobs = [case["job"] for case in data.get("cases", [])]
    return raw_jobs, candidate_profile


def _job_identity(record):
    return f"{record.source}:{record.job_id}"


def _format_list(items, empty_text="(none)"):
    return ", ".join(items) if items else empty_text


def format_text_report(result, top_n=DEFAULT_TOP_N):
    lines = []
    lines.append("OFFLINE JOB SEARCH")
    lines.append("==================")
    lines.append(f"Candidate: {result.candidate_name or '(unknown)'}")
    lines.append(f"Jobs evaluated: {result.evaluated_count}")
    lines.append(f"Eligible: {result.eligible_count}")
    lines.append(f"Ineligible: {result.ineligible_count}")
    if result.malformed_count:
        lines.append(f"Malformed/skipped: {result.malformed_count}")
    if result.duplicate_count:
        lines.append(f"Intra-source duplicates removed: {result.duplicate_count}")
    lines.append("")

    eligible_ranked = [r for r in result.ranked_jobs if r.eligible]
    top_matches = eligible_ranked[:top_n]

    lines.append("TOP MATCHES")
    lines.append("-----------")
    if not top_matches:
        lines.append("(no eligible jobs)")
    for position, record in enumerate(top_matches, start=1):
        explanation = record.score_explanation or {}
        other_sources = sorted(
            {
                entry["other_source"]
                for entry in record.duplicate_candidates
                if entry["other_source"] != record.source
            }
        )
        sources = ", ".join([record.source] + other_sources)

        lines.append(f"{position}. {record.title}")
        lines.append(f"   Company: {record.company}")
        lines.append(f"   Location: {record.canonical_location or 'Unknown'}")
        lines.append(f"   Score: {record.score}/100 ({record.priority})")
        lines.append(f"   Freshness: {record.freshness}")
        lines.append(f"   Eligibility: {record.eligibility}")
        lines.append(f"   Strong matches: {_format_list(explanation.get('strong_matches', []))}")
        lines.append(f"   Gaps: {_format_list(explanation.get('gaps', []))}")
        lines.append(f"   Sources: {sources}")
        lines.append("")

    if result.duplicate_relationships:
        lines.append("DUPLICATE CANDIDATES")
        lines.append("---------------------")
        for rel in result.duplicate_relationships:
            a = rel["job_a"]
            b = rel["job_b"]
            lines.append(
                f"- {a['source']}:{a['source_job_id']} <-> {b['source']}:{b['source_job_id']} "
                f"(confidence={rel['confidence']})"
            )
            lines.append(f"    {rel['reason']}")
        lines.append("")

    ineligible = [r for r in result.ranked_jobs if not r.eligible]
    if ineligible:
        lines.append("INELIGIBLE JOBS (excluded from top matches)")
        lines.append("--------------------------------------------")
        for record in ineligible:
            reason = "; ".join(record.eligibility_reasons) if record.eligibility_reasons else record.eligibility
            lines.append(f"- {record.company} | {record.title} | {record.eligibility} | {reason}")
        lines.append("")

    lines.append("FRESHNESS SUMMARY")
    lines.append("------------------")
    for category in ("HOT", "FRESH", "AGING", "OLD", "STALE", "UNKNOWN"):
        count = result.freshness_summary.get(category, 0)
        if count:
            lines.append(f"{category}: {count}")
    lines.append("")

    lines.append("SOURCE SUMMARY")
    lines.append("---------------")
    for source, count in sorted(result.source_summary.items()):
        lines.append(f"{source}: {count}")

    return "\n".join(lines)


def to_json_report(result):
    return {
        "candidate": result.candidate_name,
        "evaluated_count": result.evaluated_count,
        "malformed_count": result.malformed_count,
        "duplicate_count": result.duplicate_count,
        "eligible_count": result.eligible_count,
        "ineligible_count": result.ineligible_count,
        "ranked_jobs": [dataclasses.asdict(record) for record in result.ranked_jobs],
        "duplicate_relationships": result.duplicate_relationships,
        "freshness_summary": result.freshness_summary,
        "source_summary": result.source_summary,
    }


def main():
    fixture_path, as_json, top_n = _parse_args(sys.argv[1:])

    if not Path(fixture_path).exists():
        print(f"ERROR: fixture file not found: {fixture_path}")
        sys.exit(1)

    raw_jobs, candidate_profile = load_fixture(fixture_path)
    result = run_offline_pipeline(raw_jobs, candidate_profile)

    if as_json:
        print(json.dumps(to_json_report(result), indent=2))
    else:
        print(format_text_report(result, top_n=top_n))


if __name__ == "__main__":
    main()
