#!/usr/bin/env python3

"""
Daily search planner (Phase 14.6) -- reduces unnecessary daily
search-provider/API calls while reusing the EXISTING, unmodified
pipeline end to end. This module contains NO scoring, NO
normalization/dedup, NO report-rendering logic of its own: it only
decides WHICH queries get submitted, then hands that reduced plan to
the same search_submission.submit_search() / search_worker.
claim_next_queue_item() / process_queue_item() call every prior phase
used, unchanged.

Two layers of state, both read via search_history_store.py (a local,
advisory, git-ignored JSON file -- never the production DB):

  * source-level refresh intervals (Section 5): a source is DUE again
    once its own configured interval has elapsed, never permanently
    suppressed.
  * query-level fingerprint history + cooldown (Sections 3/4): the
    exact same (provider, source, role, location, freshness) request
    is skipped if it already succeeded within the cooldown window.

Consolidation (Section 6): for search-provider sources, the planner
builds ONE query per source with all target locations OR'd into the
query text (build_site_query() already accepts any free-text location
string -- this needs no change to restricted_source_registry.py or
search_provider_adapter.py). If the LAST consolidated attempt for that
exact source/role fingerprint returned zero results, the next plan
falls back to a bounded per-location split (Section 7's "if the first
query performed poorly, issue one more, then stop" adaptive rule,
applied across runs via history rather than mid-run retries).

Budgets (Section 2): MAX_SEARCH_PROVIDER_QUERIES_PER_RUN caps how many
search-provider queries THIS plan may include; MAX_SEARCH_PROVIDER_
QUERIES_PER_DAY is enforced by summing today's already-recorded
provider requests (search_provider_usage_store.py, Section 14 -- the
EXISTING Phase 14.2 store, not duplicated here). Per-provider-per-day
budgeting (MAX_SEARCH_PROVIDER_QUERIES_PER_PROVIDER_PER_DAY) is not
reimplemented here either -- it is handed to the EXISTING
SearchProviderManager via SEARCH_PROVIDER_MAX_DAILY_REQUESTS (Section
1: "Use SearchProviderManager as the ONLY provider-selection
mechanism").

Direct-sources-first (Section 11): execute_daily_plan() runs direct
sources in one submission ("wave 1"), then search-provider sources in
a second submission ("wave 2") against the SAME database -- so the
existing, unmodified cross-source dedup mechanism (which checks the DB,
not just the current batch) gets a genuine chance to recognize a
search-provider hit as already-known from wave 1 before it is ever
scored again.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLANNER_CONFIG_PATH = ROOT / "config" / "search_planner.json"

sys.path.insert(0, str(ROOT / "scripts"))

import search_history_store as history_store
import search_provider_usage_store as usage_store
import search_submission
import search_worker
from search_provider import DEFAULT_PROVIDER_ORDER
from location_taxonomy import parse_locations, LocationKind

_ENV_OVERRIDE_KEYS = {
    "max_search_provider_queries_per_run": "MAX_SEARCH_PROVIDER_QUERIES_PER_RUN",
    "max_search_provider_queries_per_day": "MAX_SEARCH_PROVIDER_QUERIES_PER_DAY",
    "max_search_provider_queries_per_provider_per_day": "MAX_SEARCH_PROVIDER_QUERIES_PER_PROVIDER_PER_DAY",
    "search_query_cooldown_hours": "SEARCH_QUERY_COOLDOWN_HOURS",
    "min_new_jobs_per_source": "MIN_NEW_JOBS_PER_SOURCE",
    "target_new_jobs_per_run": "TARGET_NEW_JOBS_PER_RUN",
    # Phase 14.7
    "max_provider_queries_per_source_per_run": "MAX_PROVIDER_QUERIES_PER_SOURCE_PER_RUN",
    "coverage_min_unique_jobs": "COVERAGE_MIN_UNIQUE_JOBS",
    "coverage_min_locations_covered": "COVERAGE_MIN_LOCATIONS_COVERED",
    "coverage_min_new_jobs": "COVERAGE_MIN_NEW_JOBS",
    # Phase 14.8
    "max_additional_query_for_unknown_location": "MAX_ADDITIONAL_QUERY_FOR_UNKNOWN_LOCATION",
}


def _normalize_city(raw_location):
    """Best-effort canonical city name for one job's raw location text
    via the EXISTING, unmodified location_taxonomy.py (Section 2: "do
    not invent location" -- never guesses, returns None for anything
    that doesn't resolve to a CITY). Reused, not reimplemented."""
    if not raw_location:
        return None
    for loc in parse_locations(raw_location):
        if loc.kind == LocationKind.CITY and loc.city:
            return loc.city
    return None


def _configured_cities(locations):
    """The candidate's configured search locations, normalized through
    the same taxonomy so "Bangalore" and "Bengaluru" are never treated
    as two different cities."""
    cities = set()
    for loc in locations:
        city = _normalize_city(loc)
        if city:
            cities.add(city)
    return cities


def load_planner_config(path=PLANNER_CONFIG_PATH):
    """Loads config/search_planner.json, then applies any matching
    environment-variable override (Section 2/4's "MUST be
    user-configurable"). Never hard-codes provider-pricing assumptions
    -- these are purely local, advisory ceilings."""
    with path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    for key, env_name in _ENV_OVERRIDE_KEYS.items():
        raw = os.environ.get(env_name)
        if raw is not None and raw.strip().isdigit():
            cfg[key] = int(raw)

    raw_completeness = os.environ.get("COVERAGE_MIN_COMPLETENESS")
    if raw_completeness is not None:
        try:
            cfg["coverage_min_completeness"] = float(raw_completeness)
        except ValueError:
            pass

    return cfg


@dataclass
class PlannedSource:
    source: str
    kind: str  # "direct" | "search_provider"
    status: str  # "DUE" | "SKIP"
    reason: str
    query_entries: list = field(default_factory=list)
    provider_display: str = ""


@dataclass
class DailyPlan:
    candidate_id: str
    role_summary: str
    locations: list
    max_job_age_days: object
    direct: list
    search_provider: list
    estimated_provider_calls: int
    max_provider_calls_per_run: int
    provider_status: dict
    skip_audit: dict  # counts by skip reason, both kinds combined
    direct_query_plan: list = field(default_factory=list)
    search_provider_query_plan: list = field(default_factory=list)


def _active_provider_display():
    """Best-effort human label for the plan preview only -- mirrors
    source_capabilities.py's own live check, never guesses a provider
    that isn't actually configured."""
    import search_provider as sp

    for name in DEFAULT_PROVIDER_ORDER:
        if sp.is_provider_configured(name) and usage_store.check_local_budget(name):
            return name.upper()
    return None


def _today_provider_request_total():
    total = 0
    for name in DEFAULT_PROVIDER_ORDER:
        total += usage_store.get_provider_usage(name)["request_count_daily"]
    return total


def build_daily_plan(
    conn,
    candidate_id,
    sources=None,
    target_roles_override=None,
    target_locations_override=None,
    max_job_age_days=None,
    force_refresh=False,
    config=None,
):
    """Read-only. Produces a DailyPlan without submitting anything or
    touching search_history_store/usage_store's persisted state (a
    preview must be safe to call repeatedly, e.g. for a GUI "show me
    the plan" button, without side effects)."""
    cfg = config or load_planner_config()
    direct_sources = cfg["direct_sources"]
    provider_sources = cfg["search_provider_sources"]

    all_sources = sources if sources is not None else (
        direct_sources + [f"{s}_SEARCH" for s in provider_sources]
    )

    search_profile, full_query_plan = search_submission.build_search_plan(
        conn,
        candidate_id,
        sources=all_sources,
        max_job_age_days=max_job_age_days,
        target_roles_override=target_roles_override,
        target_locations_override=target_locations_override,
    )

    by_source = defaultdict(list)
    for entry in full_query_plan:
        by_source[entry["source"]].append(entry)

    refresh_intervals = cfg["source_refresh_interval_hours"]
    cooldown_hours = cfg["search_query_cooldown_hours"]

    direct_plan = []
    direct_query_plan = []
    for source in direct_sources:
        entries = by_source.get(source, [])
        if not entries:
            continue
        due = force_refresh or history_store.is_source_due(source, refresh_intervals.get(source))
        if not due:
            direct_plan.append(PlannedSource(source, "direct", "SKIP", "recent successful run (refresh interval not elapsed)"))
            continue

        kept = []
        for e in entries:
            fp = history_store.compute_query_fingerprint(None, source, e["role"], e["location"], e.get("max_job_age_days"))
            if not force_refresh and history_store.is_query_in_cooldown(fp, cooldown_hours):
                continue
            kept.append(dict(e, fingerprint=fp))

        if not kept:
            direct_plan.append(PlannedSource(source, "direct", "SKIP", "all queries within cooldown window"))
        else:
            direct_plan.append(PlannedSource(source, "direct", "DUE", f"{len(kept)} quer{'y' if len(kept)==1 else 'ies'}", kept))
            direct_query_plan.extend(kept)

    active_provider = _active_provider_display()
    provider_status = {
        name: usage_store.get_provider_usage(name)["current_status"]
        for name in DEFAULT_PROVIDER_ORDER
    }

    max_per_run = cfg["max_search_provider_queries_per_run"]
    max_per_day = cfg["max_search_provider_queries_per_day"]
    already_used_today = _today_provider_request_total()
    remaining_run_budget = max_per_run
    remaining_day_budget = max(0, max_per_day - already_used_today)

    search_provider_plan = []
    search_provider_query_plan = []
    for source in provider_sources:
        registry_key = f"{source}_SEARCH"
        entries = by_source.get(registry_key) or by_source.get(source, [])
        if not entries:
            continue

        due = force_refresh or history_store.is_source_due(source, refresh_intervals.get(source))
        if not due:
            search_provider_plan.append(PlannedSource(source, "search_provider", "SKIP", "recent successful run (refresh interval not elapsed)", provider_display=active_provider or ""))
            continue

        if remaining_run_budget <= 0:
            search_provider_plan.append(PlannedSource(source, "search_provider", "SKIP", "budget-limited (per-run cap reached)", provider_display=active_provider or ""))
            continue
        if remaining_day_budget <= 0:
            search_provider_plan.append(PlannedSource(source, "search_provider", "SKIP", "budget-limited (daily cap reached)", provider_display=active_provider or ""))
            continue

        locations = sorted({e["location"] for e in entries if e["location"]})
        role = entries[0]["role"]
        max_age = entries[0]["max_job_age_days"]
        consolidated_location = " OR ".join(locations) if len(locations) > 1 else (locations[0] if locations else "")

        consolidated_fp = history_store.compute_query_fingerprint(active_provider, registry_key, role, consolidated_location, max_age)
        consolidated_record = history_store.get_query_record(consolidated_fp)
        # Adaptive fallback (Section 7): the LAST time the consolidated
        # query ran successfully, it produced zero results -> split by
        # location next time, bounded to the first 2 (by history
        # priority) to avoid re-exploding the query count.
        use_split = consolidated_record["successful_runs"] > 0 and consolidated_record["result_count"] == 0

        if use_split:
            ranked = sorted(
                entries,
                key=lambda e: history_store.query_priority_score(
                    history_store.compute_query_fingerprint(active_provider, registry_key, e["role"], e["location"], e.get("max_job_age_days"))
                ),
                reverse=True,
            )
            candidate_entries = [
                dict(e, fingerprint=history_store.compute_query_fingerprint(active_provider, registry_key, e["role"], e["location"], e.get("max_job_age_days")))
                for e in ranked[:2]
            ]
            reason_kind = "split-by-location (previous consolidated query returned zero results)"
        else:
            candidate_entries = [dict(entries[0], location=consolidated_location, fingerprint=consolidated_fp)]
            reason_kind = "consolidated (all target locations in one query)"

        if not force_refresh:
            candidate_entries = [
                e for e in candidate_entries
                if not history_store.is_query_in_cooldown(e["fingerprint"], cooldown_hours)
            ]

        if not candidate_entries:
            search_provider_plan.append(PlannedSource(source, "search_provider", "SKIP", "query within cooldown window", provider_display=active_provider or ""))
            continue

        room = min(remaining_run_budget, remaining_day_budget)
        if len(candidate_entries) > room:
            candidate_entries = candidate_entries[:room]

        if not candidate_entries:
            search_provider_plan.append(PlannedSource(source, "search_provider", "SKIP", "budget-limited (no room remaining)", provider_display=active_provider or ""))
            continue

        remaining_run_budget -= len(candidate_entries)
        remaining_day_budget -= len(candidate_entries)

        search_provider_plan.append(
            PlannedSource(
                source, "search_provider", "DUE",
                f"{len(candidate_entries)} quer{'y' if len(candidate_entries)==1 else 'ies'} ({reason_kind})",
                candidate_entries, provider_display=active_provider or "(none configured)",
            )
        )
        search_provider_query_plan.extend(candidate_entries)

    for i, e in enumerate(direct_query_plan):
        e["sequence"] = i
    for i, e in enumerate(search_provider_query_plan):
        e["sequence"] = i

    skip_audit = defaultdict(int)
    for p in direct_plan + search_provider_plan:
        if p.status == "SKIP":
            skip_audit[p.reason.split(" (")[0]] += 1

    return DailyPlan(
        candidate_id=candidate_id,
        role_summary=", ".join(sorted(set(search_profile.target_roles))),
        locations=sorted(set(search_profile.target_locations)),
        max_job_age_days=search_profile.max_job_age_days,
        direct=direct_plan,
        search_provider=search_provider_plan,
        estimated_provider_calls=len(search_provider_query_plan),
        max_provider_calls_per_run=max_per_run,
        provider_status=provider_status,
        skip_audit=dict(skip_audit),
        direct_query_plan=direct_query_plan,
        search_provider_query_plan=search_provider_query_plan,
    )


def format_plan_text(plan: DailyPlan) -> str:
    lines = ["Search Plan", "-----------", "Direct:"]
    for p in plan.direct:
        lines.append(f"  {p.source:<10}  {p.status:<4}  {p.reason}")
    lines.append("")
    lines.append("Search Provider:")
    for p in plan.search_provider:
        provider_tag = f"   {p.provider_display}" if p.provider_display else ""
        lines.append(f"  {p.source:<10}  {p.status:<4}  {p.reason}{provider_tag}")
    lines.append("")
    lines.append(f"Estimated provider calls:\n  {plan.estimated_provider_calls}")
    lines.append("")
    lines.append(f"Maximum allowed (per run):\n  {plan.max_provider_calls_per_run}")
    lines.append("")
    total_queries = len(plan.direct_query_plan) + len(plan.search_provider_query_plan)
    lines.append(f"Execute? {'YES' if total_queries > 0 else 'NO -- nothing due'}")
    return "\n".join(lines)


@dataclass
class WaveResult:
    name: str
    sources: list
    query_count: int
    work_result: object = None
    new_jobs_by_source: dict = field(default_factory=dict)
    known_jobs_by_source: dict = field(default_factory=dict)
    # Phase 14.7: per new job this wave persisted, {source: [(location_raw, completeness_or_None), ...]}
    new_job_details_by_source: dict = field(default_factory=dict)
    # Phase 14.8: {source: {job_id, ...}} -- every job_id present after
    # this wave (new AND rediscovered-known), for cumulative-unique
    # tracking that never double-counts a rediscovery.
    job_ids_by_source: dict = field(default_factory=dict)


@dataclass
class ExecutionResult:
    plan: DailyPlan
    waves: list
    total_new_jobs: int
    total_known_jobs: int
    coverage: dict = field(default_factory=dict)  # {board_source: {"covered_cities": [...], "still_uncovered": [...]}}
    adaptive_metrics: dict = field(default_factory=dict)


def _job_snapshot(conn, sources):
    """{(job_id, source): (location_raw, completeness)} for every
    currently-persisted job under any of `sources` -- the basis for
    every before/after new-vs-known diff in this module."""
    if not sources:
        return {}
    placeholders = ",".join("?" for _ in sources)
    rows = conn.execute(
        f"SELECT job_id, source, location, completeness FROM jobs WHERE source IN ({placeholders})",
        list(sources),
    ).fetchall()
    return {(r[0], r[1]): (r[2], r[3]) for r in rows}


def _job_id_set(conn, sources):
    return set(_job_snapshot(conn, sources).keys())


def _run_one_wave(db_path, candidate_id, name, sources, query_plan, target_roles_override, target_locations_override, max_job_age_days, job_id_sources=None):
    """sources: what gets submitted (may be *_SEARCH registry keys for
    search-provider sources). job_id_sources: what jobs.source actually
    persists as (the real board name, e.g. "CUTSHORT" not
    "CUTSHORT_SEARCH" -- see Phase 14.5's finding) -- defaults to
    `sources` when the two are the same (the direct-source wave)."""
    job_id_sources = job_id_sources if job_id_sources is not None else sources

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        before = _job_snapshot(conn, job_id_sources)
    finally:
        conn.close()

    submit_result = search_submission.submit_search(
        db_path, candidate_id,
        sources=sources,
        target_roles_override=target_roles_override,
        target_locations_override=target_locations_override,
        max_job_age_days=max_job_age_days,
        query_plan_override=query_plan,
    )

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        claimed = search_worker.claim_next_queue_item(conn, candidate_id=candidate_id)
        conn.commit()
        work_result = search_worker.process_queue_item(conn, claimed)
        conn.commit()
        after = _job_snapshot(conn, job_id_sources)
    finally:
        conn.close()

    new_by_source = defaultdict(int)
    known_by_source = defaultdict(int)
    new_details_by_source = defaultdict(list)
    job_ids_by_source = defaultdict(set)
    for key, (location_raw, completeness) in after.items():
        job_id, source = key
        job_ids_by_source[source].add(job_id)
        if key in before:
            known_by_source[source] += 1
        else:
            new_by_source[source] += 1
            new_details_by_source[source].append((location_raw, completeness))

    wave = WaveResult(
        name=name, sources=sources, query_count=len(query_plan),
        work_result=work_result,
        new_jobs_by_source=dict(new_by_source),
        known_jobs_by_source=dict(known_by_source),
        new_job_details_by_source=dict(new_details_by_source),
        job_ids_by_source=dict(job_ids_by_source),
    )
    return wave, submit_result


def _registry_to_board(registry_source):
    return registry_source[: -len("_SEARCH")] if registry_source.endswith("_SEARCH") else registry_source


def _coverage_sufficient(cumulative, configured_cities, cfg):
    """Phase 14.8: insufficiency is now judged along TWO independent
    axes, never conflated:

      * volume reasons (unique/new job counts, completeness) -- always
        a legitimate reason to expand, regardless of location evidence.
      * location reasons (fewer than the configured minimum number of
        cities have >=1 job with a genuinely KNOWN location) -- this
        axis reflects "not yet CONFIRMED for these cities," never
        "zero jobs for these cities" (Section 7): a job whose location
        could not be parsed contributes to unknown_location_jobs, not
        to a false covered/uncovered signal either way.

    Returns a dict: sufficient, sufficient_on_volume,
    non_location_reasons, location_reasons, missing (cities with no
    KNOWN-location job yet -- "not yet confirmed," not "proven zero")."""
    missing = configured_cities - cumulative["covered_cities"]

    non_location_reasons = []
    if cumulative["unique_jobs"] < cfg["coverage_min_unique_jobs"]:
        non_location_reasons.append(f"fewer than {cfg['coverage_min_unique_jobs']} unique jobs ({cumulative['unique_jobs']})")
    if cumulative["new_jobs"] < cfg["coverage_min_new_jobs"]:
        non_location_reasons.append(f"fewer than {cfg['coverage_min_new_jobs']} genuinely new jobs ({cumulative['new_jobs']})")
    if cumulative["avg_completeness"] is not None and cumulative["avg_completeness"] < cfg["coverage_min_completeness"]:
        non_location_reasons.append(f"completeness below {cfg['coverage_min_completeness']} ({round(cumulative['avg_completeness'], 2)})")

    location_reasons = []
    min_locs = min(cfg["coverage_min_locations_covered"], len(configured_cities)) if configured_cities else 0
    if len(cumulative["covered_cities"]) < min_locs:
        location_reasons.append(f"fewer than {min_locs} configured locations confirmed by a KNOWN-location job ({len(cumulative['covered_cities'])})")
    if missing:
        location_reasons.append(
            f"no KNOWN-location job yet for: {', '.join(sorted(missing))} "
            f"(not yet confirmed -- {cumulative['unknown_location_jobs']} job(s) this source found so far have UNKNOWN location and may or may not be in these cities)"
        )

    return {
        "sufficient": not (non_location_reasons or location_reasons),
        "sufficient_on_volume": not non_location_reasons,
        "non_location_reasons": non_location_reasons,
        "location_reasons": location_reasons,
        "missing": missing,
    }


def _update_cumulative(cumulative, wave: WaveResult, board):
    """Phase 14.8 fix: unique_jobs is a UNION of job_ids seen across
    every round so far this run, not a running sum of new+known counts
    -- a job rediscovered ("known") in round 2 is the SAME job counted
    when it was first found ("new") in round 1, and must not inflate
    the total a second time. new_jobs stays a simple running sum
    because a given job_id can only ever be "new" once, the round that
    first inserts it -- no double-count risk there."""
    details = wave.new_job_details_by_source.get(board, [])
    new_count = wave.new_jobs_by_source.get(board, 0)

    cumulative["job_ids"] |= wave.job_ids_by_source.get(board, set())
    cumulative["unique_jobs"] = len(cumulative["job_ids"])
    cumulative["new_jobs"] += new_count
    for location_raw, completeness in details:
        # Section 1/2: actual_job_location_status is KNOWN only when
        # location_taxonomy.py can genuinely parse a city from the
        # search result itself -- never inferred from which location
        # the query targeted. Section 2's "otherwise UNKNOWN" is this
        # else branch, exactly.
        city = _normalize_city(location_raw)
        if city:
            cumulative["covered_cities"].add(city)
            cumulative["known_location_jobs"] += 1
        else:
            cumulative["unknown_location_jobs"] += 1
        if completeness is not None:
            cumulative["_completeness_values"].append(completeness)
    cumulative["avg_completeness"] = (
        sum(cumulative["_completeness_values"]) / len(cumulative["_completeness_values"])
        if cumulative["_completeness_values"] else None
    )
    return cumulative


def execute_daily_plan(
    db_path, candidate_id, plan: DailyPlan,
    target_roles_override=None, target_locations_override=None,
    cooldown_hours=None, config=None,
):
    """Executes plan.direct_query_plan (wave 1, unchanged since Phase
    14.6) then plan.search_provider_query_plan (wave 2, the
    consolidated round-1 queries), then -- Phase 14.7 -- zero or more
    additional ADAPTIVE rounds per search-provider source whose
    coverage is insufficient (Section 3), each combining every
    still-uncovered/under-covered configured location into ONE more
    targeted query, bounded by max_provider_queries_per_source_per_run
    (Section 5) and the existing Section 6 run/day budgets. Every round
    reuses the SAME, unmodified submit_search -> claim_next_queue_item
    -> process_queue_item pipeline -- never a second one."""
    cfg = config or load_planner_config()
    cooldown_hours = cooldown_hours if cooldown_hours is not None else cfg["search_query_cooldown_hours"]

    waves = []
    adaptive_metrics = {
        "consolidated_queries": 0,
        "adaptive_queries": 0,
        "queries_saved": 0,
        "locations_initially_covered": {},
        "locations_filled_by_adaptive_queries": {},
        # Phase 14.8: "not yet confirmed by a KNOWN-location job," NEVER
        # "proven zero" -- see _coverage_sufficient()'s docstring and
        # Section 7 of the Phase 14.8 report. Kept under this same key
        # for continuity with Phase 14.7's reporting.
        "locations_still_uncovered": {},
        "query_expansion_reasons": {},
        # Phase 14.8 additions
        "known_location_jobs": {},
        "unknown_location_jobs": {},
        "query_target_locations": {},
        "locations_verified": {},
        "locations_unknown_count": {},
        "queries_triggered_by_location_gap": 0,
        "queries_not_triggered_due_to_unknown_location": 0,
    }
    coverage_report = {}

    if plan.direct_query_plan:
        direct_sources = sorted({e["source"] for e in plan.direct_query_plan})
        wave, _ = _run_one_wave(
            db_path, candidate_id, "direct", direct_sources, plan.direct_query_plan,
            target_roles_override, target_locations_override, plan.max_job_age_days,
        )
        waves.append(wave)
        _record_wave(wave, plan.direct_query_plan, cooldown_hours)

    if plan.search_provider_query_plan:
        configured_cities = _configured_cities(plan.locations)
        max_per_source = cfg["max_provider_queries_per_source_per_run"]
        active_provider = _active_provider_display()

        provider_registry_sources = sorted({e["source"] for e in plan.search_provider_query_plan})
        provider_board_sources = sorted({_registry_to_board(s) for s in provider_registry_sources})

        # Round 1: the consolidated queries build_daily_plan() already built.
        wave, _ = _run_one_wave(
            db_path, candidate_id, "search_provider", provider_registry_sources, plan.search_provider_query_plan,
            target_roles_override, target_locations_override, plan.max_job_age_days,
            job_id_sources=provider_board_sources,
        )
        waves.append(wave)
        _record_wave(wave, plan.search_provider_query_plan, cooldown_hours, registry_to_board=True)
        adaptive_metrics["consolidated_queries"] = len(plan.search_provider_query_plan)

        entries_by_board = {_registry_to_board(e["source"]): e for e in plan.search_provider_query_plan}
        used_queries = {board: 1 for board in entries_by_board}
        used_location_gap_queries = {board: 0 for board in entries_by_board}
        max_unknown_location_queries = cfg.get("max_additional_query_for_unknown_location", 1)
        cumulative = {}
        for board in provider_board_sources:
            cumulative[board] = {
                "unique_jobs": 0, "new_jobs": 0, "covered_cities": set(), "job_ids": set(),
                "known_location_jobs": 0, "unknown_location_jobs": 0,
                "avg_completeness": None, "_completeness_values": [],
            }
            _update_cumulative(cumulative[board], wave, board)
            adaptive_metrics["locations_initially_covered"][board] = sorted(cumulative[board]["covered_cities"])
            adaptive_metrics["query_target_locations"][board] = [entries_by_board[board]["location"]]

        remaining_run_budget = cfg["max_search_provider_queries_per_run"] - len(plan.search_provider_query_plan)

        # Every city-combination already attempted THIS execution, per
        # board -- seeded with round 1's own consolidated combination.
        # Degenerate case: when NOTHING is covered yet, "missing" ==
        # every configured city, which is textually identical to round
        # 1's own query. Retrying that exact text would (a) be
        # deterministically pointless against a real provider and (b)
        # collide with the cooldown record round 1 JUST wrote for that
        # same fingerprint. So when the naive target set was already
        # tried this run, the lowest-priority city is dropped and the
        # (now-smaller, genuinely different) subset is tried instead --
        # a real, cheaper probe, never a repeat.
        tried_city_sets = {}
        for board, template_entry in entries_by_board.items():
            consolidated_cities = {_normalize_city(c) or c for c in template_entry["location"].split(" OR ")}
            tried_city_sets[board] = {frozenset(consolidated_cities)}

        round_num = 1
        while round_num < max_per_source:
            round_num += 1
            round_entries = []

            for board, template_entry in entries_by_board.items():
                if used_queries.get(board, 0) >= max_per_source:
                    continue
                cov = _coverage_sufficient(cumulative[board], configured_cities, cfg)
                if cov["sufficient"]:
                    continue

                # Phase 14.8, Section 3: once volume/new-jobs/
                # completeness are ALL already sufficient, the only
                # remaining reason to expand is a location gap -- and
                # that gap may just be missing metadata (UNKNOWN
                # location), not proof of zero jobs. Do not let that
                # alone drive unlimited expansion: cap it separately.
                is_location_only_gap = cov["sufficient_on_volume"] and bool(cov["location_reasons"])
                if is_location_only_gap and used_location_gap_queries[board] >= max_unknown_location_queries:
                    adaptive_metrics["queries_not_triggered_due_to_unknown_location"] += 1
                    continue

                if remaining_run_budget <= 0:
                    # Section 6: never exceed budget -- stop cleanly and
                    # let locations_still_uncovered report what's left.
                    break

                reasons = cov["non_location_reasons"] + cov["location_reasons"]
                missing = cov["missing"]
                target_cities = sorted(missing) if missing else sorted(configured_cities)
                # Priority order (Section 7): locations already known to
                # be zero-result come first -- with everything currently
                # missing this is a tie, so the deterministic fallback
                # tie-break is alphabetical, per this module's "keep it
                # simple, explainable" mandate (Section 10 of Phase
                # 14.6/Section 9 here: no ML, no hidden heuristics).
                while target_cities and frozenset(target_cities) in tried_city_sets[board]:
                    target_cities = target_cities[:-1]
                if not target_cities:
                    continue

                target_location_text = " OR ".join(target_cities) if len(target_cities) > 1 else target_cities[0]
                fp = history_store.compute_query_fingerprint(
                    active_provider, template_entry["source"], template_entry["role"], target_location_text, template_entry.get("max_job_age_days"),
                )
                if history_store.is_query_in_cooldown(fp, cooldown_hours):
                    tried_city_sets[board].add(frozenset(target_cities))
                    continue

                tried_city_sets[board].add(frozenset(target_cities))
                round_entries.append(dict(template_entry, location=target_location_text, fingerprint=fp))
                used_queries[board] = used_queries.get(board, 0) + 1
                remaining_run_budget -= 1
                adaptive_metrics["query_expansion_reasons"][board] = reasons
                adaptive_metrics["query_target_locations"].setdefault(board, []).append(target_location_text)
                if is_location_only_gap:
                    used_location_gap_queries[board] += 1
                    adaptive_metrics["queries_triggered_by_location_gap"] += 1

            if not round_entries:
                break

            for i, e in enumerate(round_entries):
                e["sequence"] = i

            round_registry_sources = sorted({e["source"] for e in round_entries})
            round_board_sources = sorted({_registry_to_board(s) for s in round_registry_sources})
            round_wave, _ = _run_one_wave(
                db_path, candidate_id, f"search_provider_adaptive_round{round_num}", round_registry_sources, round_entries,
                target_roles_override, target_locations_override, plan.max_job_age_days,
                job_id_sources=round_board_sources,
            )
            waves.append(round_wave)
            _record_wave(round_wave, round_entries, cooldown_hours, registry_to_board=True)
            adaptive_metrics["adaptive_queries"] += len(round_entries)

            for board in round_board_sources:
                before_cities = set(cumulative[board]["covered_cities"])
                _update_cumulative(cumulative[board], round_wave, board)
                filled = cumulative[board]["covered_cities"] - before_cities
                if filled:
                    adaptive_metrics["locations_filled_by_adaptive_queries"].setdefault(board, set()).update(filled)

        for board in provider_board_sources:
            coverage_report[board] = {
                "covered_cities": sorted(cumulative[board]["covered_cities"]),
                # Phase 14.8: "not yet confirmed by a KNOWN-location
                # job this run" -- never asserted as proven-zero
                # (Section 7). See known/unknown_location_jobs below
                # for why a city may sit here despite real jobs existing.
                "still_uncovered": sorted(configured_cities - cumulative[board]["covered_cities"]),
                "unique_jobs": cumulative[board]["unique_jobs"],
                "new_jobs": cumulative[board]["new_jobs"],
                "known_location_jobs": cumulative[board]["known_location_jobs"],
                "unknown_location_jobs": cumulative[board]["unknown_location_jobs"],
                "avg_completeness": cumulative[board]["avg_completeness"],
                "queries_used": used_queries.get(board, 1),
                "queries_used_for_location_gap": used_location_gap_queries.get(board, 0),
            }
            adaptive_metrics["locations_still_uncovered"][board] = coverage_report[board]["still_uncovered"]
            adaptive_metrics["known_location_jobs"][board] = cumulative[board]["known_location_jobs"]
            adaptive_metrics["unknown_location_jobs"][board] = cumulative[board]["unknown_location_jobs"]
            adaptive_metrics["locations_verified"][board] = sorted(cumulative[board]["covered_cities"])
            adaptive_metrics["locations_unknown_count"][board] = cumulative[board]["unknown_location_jobs"]

        adaptive_metrics["locations_filled_by_adaptive_queries"] = {
            k: sorted(v) for k, v in adaptive_metrics["locations_filled_by_adaptive_queries"].items()
        }
        old_style_search_provider_queries = len(cfg["search_provider_sources"]) * max(len(plan.locations), 1)
        actual_search_provider_queries = adaptive_metrics["consolidated_queries"] + adaptive_metrics["adaptive_queries"]
        adaptive_metrics["queries_saved"] = max(0, old_style_search_provider_queries - actual_search_provider_queries)

    total_new = sum(sum(w.new_jobs_by_source.values()) for w in waves)
    total_known = sum(sum(w.known_jobs_by_source.values()) for w in waves)

    return ExecutionResult(
        plan=plan, waves=waves, total_new_jobs=total_new, total_known_jobs=total_known,
        coverage=coverage_report, adaptive_metrics=adaptive_metrics,
    )


def _record_wave(wave: WaveResult, query_plan, cooldown_hours, registry_to_board=False):
    """Persists per-query and per-source history from one executed
    wave. registry_to_board strips a trailing "_SEARCH" so history is
    keyed by the real board name (LINKEDIN), matching how jobs.source
    is actually persisted (see Phase 14.5's finding)."""
    by_source_entries = defaultdict(list)
    for e in query_plan:
        by_source_entries[e["source"]].append(e)

    active_provider = _active_provider_display()
    errors = wave.work_result.errors if wave.work_result else []
    had_error = bool(wave.work_result and wave.work_result.status not in ("COMPLETED", "PARTIAL"))

    for registry_source, entries in by_source_entries.items():
        board_source = registry_source[:-len("_SEARCH")] if registry_to_board and registry_source.endswith("_SEARCH") else registry_source
        new_count = wave.new_jobs_by_source.get(board_source, 0)
        known_count = wave.known_jobs_by_source.get(board_source, 0)

        for e in entries:
            fp = e.get("fingerprint") or history_store.compute_query_fingerprint(
                active_provider if registry_to_board else None, registry_source, e["role"], e["location"], e.get("max_job_age_days"),
            )
            history_store.record_query_result(
                fp,
                provider=active_provider if registry_to_board else None,
                source=registry_source,
                role=e["role"],
                location=e["location"],
                query_text=e["location"],
                result_count=new_count + known_count,
                new_result_count=new_count,
                error_state="ERROR" if had_error else None,
            )

        history_store.record_source_run(
            board_source, new_count + known_count, new_count,
            error_state="ERROR" if had_error else None,
        )
