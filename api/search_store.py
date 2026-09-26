"""
Phase 9 saved-search persistence + execution orchestration. Reuses,
unmodified in behavior for every existing caller:
  - search_submission.submit_search() (via its new, additive
    target_roles_override / target_locations_override /
    work_models_override parameters)
  - search_worker.run_once()
  - source_registry.list_sources() / get_adapter_status()
No second query-planning, scoring, or worker implementation.
"""

import json
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from source_adapter import AdapterStatus
import source_registry
import source_capabilities
from search_submission import SearchSubmissionError, submit_search
import search_worker


class SearchStoreError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def enabled_sources():
    """Every currently-ENABLED, non-MOCK source -- the only sources a
    saved search is ever allowed to request. Mirrors
    search_profile._default_sources()'s own filter exactly."""
    return [
        s for s in source_registry.list_sources()
        if s != "MOCK" and source_registry.get_adapter_status(s) == AdapterStatus.ENABLED
    ]


def source_capability_summary():
    """Full source capability matrix for the GUI/API -- enabled sources
    plus every unavailable one with its reason (Phase L). Never a
    hardcoded list; reads scripts/source_capabilities.py live."""
    return {
        "enabled": source_capabilities.list_enabled_sources(),
        "unavailable": source_capabilities.list_unavailable_sources(),
    }


def validate_sources(sources):
    if sources is None:
        return None
    allowed = set(enabled_sources())
    invalid = [s for s in sources if s not in allowed]
    if invalid:
        raise SearchStoreError(
            f"Source(s) not currently enabled: {invalid}. Enabled sources: {sorted(allowed)}"
        )
    return list(sources)


_VALID_SEARCH_TYPES = frozenset({"USER", "TEST", "SYSTEM"})


def validate_search_type(search_type):
    """None (the overwhelming majority of real calls -- a real user
    never sets this) lets the DB column's own DEFAULT 'USER' apply,
    never guessed/inferred here. An explicit value must be one of the
    three known types -- see migrate_v10_search_type.py."""
    if search_type is None:
        return None
    if search_type not in _VALID_SEARCH_TYPES:
        raise SearchStoreError(f"Invalid search_type: {search_type!r}. Must be one of {sorted(_VALID_SEARCH_TYPES)}")
    return search_type


def create_saved_search(conn, candidate_id, payload):
    sources = validate_sources(payload.sources)
    # Defaulted to "USER" in Python (rather than relying on the DB
    # column's own DEFAULT) because this INSERT always passes an
    # explicit value for every column -- an explicit bound NULL would
    # NOT fall back to the column DEFAULT (SQLite only applies DEFAULT
    # when a column is OMITTED from the INSERT entirely). A real user
    # creating a search through the normal UI never sets this
    # themselves; it resolves to "USER" here exactly as if the column
    # DEFAULT had applied.
    search_type = validate_search_type(getattr(payload, "search_type", None)) or "USER"
    saved_search_id = "search_" + uuid.uuid4().hex[:12]
    now = _now()

    conn.execute(
        """
        INSERT INTO saved_searches (
            saved_search_id, candidate_id, name,
            target_roles_json, target_locations_json, work_models_json,
            employment_type, minimum_experience_years, maximum_experience_years,
            salary_expectation_min, salary_expectation_max, salary_currency,
            minimum_match_score, max_job_age_days, sources_json,
            skills_json, schedule_json, profile_version, search_type,
            status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
        """,
        (
            saved_search_id,
            candidate_id,
            payload.name,
            json.dumps(payload.target_roles),
            json.dumps(payload.target_locations),
            json.dumps(payload.work_models),
            payload.employment_type,
            payload.minimum_experience_years,
            payload.maximum_experience_years,
            payload.salary_expectation_min,
            payload.salary_expectation_max,
            payload.salary_currency,
            payload.minimum_match_score,
            payload.max_job_age_days,
            json.dumps(sources) if sources is not None else None,
            json.dumps(payload.skills or []),
            json.dumps(payload.schedule.model_dump()) if payload.schedule else json.dumps({"enabled": False, "frequency": None}),
            payload.profile_version,
            search_type,
            now,
            now,
        ),
    )
    conn.commit()
    return saved_search_id


def _row_to_dict(row):
    d = dict(row)
    for key in ("target_roles_json", "target_locations_json", "work_models_json", "sources_json", "skills_json"):
        out_key = key[: -len("_json")]
        raw = d.pop(key, None)
        d[out_key] = json.loads(raw) if raw else ([] if out_key != "sources" else None)
    schedule_raw = d.pop("schedule_json", None)
    d["schedule"] = json.loads(schedule_raw) if schedule_raw else {"enabled": False, "frequency": None}
    return d


def get_saved_search(conn, saved_search_id):
    row = conn.execute(
        "SELECT * FROM saved_searches WHERE saved_search_id = ?", (saved_search_id,)
    ).fetchone()
    if row is None:
        raise SearchStoreError(f"Unknown saved search: {saved_search_id!r}")
    return _row_to_dict(row)


def get_saved_search_for_candidate(conn, saved_search_id, candidate_id):
    """
    Ownership-enforcing variant of get_saved_search() -- every API route
    that takes a bare search_id (not already nested under a verified
    :candidate_id path) must use this, never get_saved_search()
    directly, so candidate A can never read/run/delete/report on
    candidate B's saved search merely by knowing or guessing its id.

    Raises the SAME SearchStoreError (-> 404 at the API layer, exactly
    like an unknown search_id) whether the search_id doesn't exist at
    all or belongs to a different candidate -- deliberately
    indistinguishable, so a caller can never use the error to probe
    which search_ids exist for another candidate.
    """
    saved_search = get_saved_search(conn, saved_search_id)
    if saved_search["candidate_id"] != candidate_id:
        raise SearchStoreError(f"Unknown saved search: {saved_search_id!r}")
    return saved_search


def list_saved_searches(conn, candidate_id, include_archived=False, search_types=("USER",)):
    """
    search_types (item 3 fix -- test-data isolation): restricts the
    listing to these saved_searches.search_type values. Defaults to
    ("USER",) -- the normal user-facing /searches page and its API
    show ONLY real user searches, never a TEST/SYSTEM one, by
    explicit semantic field, not brittle name matching (see
    migrate_v10_search_type.py). Pass search_types=None for every
    type (used by this project's own diagnostics, never the normal
    user-facing endpoint).
    """
    query = "SELECT * FROM saved_searches WHERE candidate_id = ?"
    params = [candidate_id]
    if not include_archived:
        query += " AND status = 'ACTIVE'"
    if search_types is not None:
        query += f" AND search_type IN ({','.join('?' for _ in search_types)})"
        params.extend(search_types)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_saved_search(conn, saved_search_id, payload):
    current = get_saved_search(conn, saved_search_id)

    fields = {
        "name": payload.name,
        "target_roles_json": json.dumps(payload.target_roles) if payload.target_roles is not None else None,
        "target_locations_json": json.dumps(payload.target_locations) if payload.target_locations is not None else None,
        "work_models_json": json.dumps(payload.work_models) if payload.work_models is not None else None,
        "employment_type": payload.employment_type,
        "minimum_experience_years": payload.minimum_experience_years,
        "maximum_experience_years": payload.maximum_experience_years,
        "salary_expectation_min": payload.salary_expectation_min,
        "salary_expectation_max": payload.salary_expectation_max,
        "salary_currency": payload.salary_currency,
        "minimum_match_score": payload.minimum_match_score,
        "max_job_age_days": payload.max_job_age_days,
    }

    if payload.sources is not None:
        fields["sources_json"] = json.dumps(validate_sources(payload.sources))

    if payload.skills is not None:
        fields["skills_json"] = json.dumps(payload.skills)

    if payload.schedule is not None:
        fields["schedule_json"] = json.dumps(payload.schedule.model_dump())

    set_clauses = []
    params = []
    for key, value in fields.items():
        if value is not None:
            set_clauses.append(f"{key} = ?")
            params.append(value)

    # profile_version is deliberately handled OUTSIDE the generic
    # skip-if-None loop above: None is a genuine, meaningful value here
    # ("un-pin back to Current profile"), not "field omitted" -- every
    # other field's skip-if-None behavior means "leave unchanged",
    # which would make it impossible to ever clear a pin once set.
    # model_fields_set (Pydantic v2) distinguishes "the caller's JSON
    # body explicitly included this key" from "the caller omitted it
    # entirely" -- only the former updates the column, to None or
    # otherwise; an edit request that never mentions profile_version at
    # all leaves the existing pin (or lack of one) untouched.
    if "profile_version" in payload.model_fields_set:
        set_clauses.append("profile_version = ?")
        params.append(payload.profile_version)

    if not set_clauses:
        return current

    set_clauses.append("updated_at = ?")
    params.append(_now())
    params.append(saved_search_id)

    conn.execute(
        f"UPDATE saved_searches SET {', '.join(set_clauses)} WHERE saved_search_id = ?",
        params,
    )
    conn.commit()
    return get_saved_search(conn, saved_search_id)


def archive_saved_search(conn, saved_search_id):
    get_saved_search(conn, saved_search_id)
    conn.execute(
        "UPDATE saved_searches SET status = 'ARCHIVED', updated_at = ? WHERE saved_search_id = ?",
        (_now(), saved_search_id),
    )
    conn.commit()


def _run_worker_background(db_path, search_run_id, candidate_id):
    try:
        search_worker.run_once(str(db_path), search_run_id=search_run_id, max_items=1)
    except Exception:
        # process_queue_item()/_finalize_queue_item() already record
        # failures onto the search_runs row itself; a thread-level
        # exception here (e.g. a bug before that point) must not crash
        # the process -- swallow it, the run's status simply never
        # leaves QUEUED/RUNNING and GET /api/runs/{id} reflects that.
        return

    # Phase I step 15: generate the EXISTING 9-sheet Excel report right
    # after the run finishes, so it is already sitting on disk the
    # moment the user opens the results/dashboard page -- reuses
    # generate_run_report.generate() exactly as the on-demand
    # /api/candidates/{id}/report endpoint does; no second report
    # implementation. A failure here must never mark the search run
    # itself as failed -- the run's own status already reflects the
    # actual search outcome.
    try:
        import generate_run_report

        out_dir = Path(db_path).resolve().parent / "reports_dev"
        out_dir.mkdir(parents=True, exist_ok=True)
        generate_run_report.generate(str(db_path), candidate_id, str(out_dir / f"{candidate_id}_report.xlsx"))
    except Exception:
        pass


def recover_orphaned_runs(conn):
    """
    Startup recovery: mark every run left QUEUED/RUNNING as FAILED
    with an explicit, honest reason. Safe unconditionally in this
    project's architecture -- every worker is either a short-lived CLI
    invocation that already exited by the time anything else runs, or
    a daemon thread spawned inside trigger_run()'s own API request,
    tied to THAT process's lifetime (scripts/run_search_worker.py's own
    docstring: "There is no daemon/loop mode... no scheduler/daemon of
    any kind exists yet in this project"). Neither can survive a
    process restart. So by the time THIS runs (at a fresh process's own
    startup), any row still QUEUED/RUNNING cannot have a genuinely live
    worker behind it -- it is unconditionally orphaned, never a false
    positive, and this never risks terminating a real in-progress run
    (a real one is, by definition, running inside the same process that
    is calling this function for the first time).

    Reuses the existing, already-recognized "FAILED" terminal status
    (never a new status value, so every existing status-handling code
    path elsewhere is untouched) for both search_runs and search_queue,
    exactly like _finalize_queue_item() already does for every other
    failure reason.

    Returns the number of runs recovered (0 if none were orphaned).
    """
    reason = "Worker process was not running after API restart."
    now = _now()
    rows = conn.execute(
        "SELECT search_run_id FROM search_runs WHERE status IN ('QUEUED', 'RUNNING')"
    ).fetchall()
    if not rows:
        return 0

    run_ids = [r[0] for r in rows]
    placeholders = ",".join("?" for _ in run_ids)
    conn.execute(
        f"""
        UPDATE search_runs
        SET status = 'FAILED', completed_at = ?, updated_at = ?, error_message = ?
        WHERE search_run_id IN ({placeholders})
        """,
        [now, now, reason, *run_ids],
    )
    conn.execute(
        f"UPDATE search_queue SET status = 'FAILED', completed_at = ? WHERE search_run_id IN ({placeholders})",
        [now, *run_ids],
    )
    conn.commit()
    return len(run_ids)


def get_active_run_for_search(conn, saved_search_id):
    """
    The current in-progress (QUEUED or RUNNING) run for this saved
    search, if any -- a plain persisted-DB-state read (never an
    in-memory flag), so it stays correct across API process reloads/
    restarts, exactly like every other run-status check in this
    module. Returns (search_run_id, status), or None if this search
    has no run currently in a non-terminal state -- a run that has
    already reached COMPLETED/PARTIAL/FAILED/BLOCKED never blocks a
    new one. Used by trigger_run() to prevent two concurrent runs for
    the same saved search (found via a real end-to-end test: nothing
    previously stopped a second "Run Now" while the first was still
    executing).
    """
    row = conn.execute(
        """
        SELECT sr.search_run_id, sr.status
        FROM saved_search_runs ssr
        JOIN search_runs sr ON sr.search_run_id = ssr.search_run_id
        WHERE ssr.saved_search_id = ? AND sr.status IN ('QUEUED', 'RUNNING')
        ORDER BY sr.created_at DESC
        LIMIT 1
        """,
        (saved_search_id,),
    ).fetchone()
    return (row[0], row[1]) if row else None


def trigger_run(conn, db_path, saved_search):
    """
    Submit `saved_search` for execution: build a search plan using the
    candidate's CONFIRMED profile (identity/skills/experience) with
    THIS saved search's own target_roles/target_locations/work_models
    substituted in via submit_search()'s additive override parameters,
    then start the existing search_worker in a background thread so
    this call returns immediately.

    If this saved search already has a QUEUED/RUNNING run (see
    get_active_run_for_search()), does NOT submit a new search plan or
    spawn a new worker thread -- returns that existing run's own id/
    status instead, with already_running=True, so the caller can tell
    the user "search is already running" rather than silently starting
    a second, resource-contending run against the same external sites.

    Returns {"search_run_id": str, "status": str, "already_running": bool}.
    """
    active = get_active_run_for_search(conn, saved_search["saved_search_id"])
    if active is not None:
        active_run_id, active_status = active
        return {"search_run_id": active_run_id, "status": active_status, "already_running": True}

    try:
        result = submit_search(
            db_path=str(db_path),
            candidate_id=saved_search["candidate_id"],
            sources=saved_search["sources"],
            minimum_match_score=saved_search["minimum_match_score"],
            minimum_experience_years=saved_search["minimum_experience_years"],
            maximum_experience_years=saved_search["maximum_experience_years"],
            max_job_age_days=saved_search["max_job_age_days"],
            target_roles_override=saved_search["target_roles"],
            target_locations_override=saved_search["target_locations"],
            work_models_override=saved_search["work_models"] or None,
            # None (the default/unset case) preserves today's existing
            # behavior exactly: always use whichever profile is
            # currently active. Only a search explicitly pinned to a
            # historical version (see migrate_v8_resume_profile_
            # traceability.py) ever overrides this.
            profile_version_override=saved_search.get("profile_version"),
        )
    except SearchSubmissionError as error:
        raise SearchStoreError(str(error)) from error

    conn.execute(
        "INSERT OR IGNORE INTO saved_search_runs (saved_search_id, search_run_id, created_at) VALUES (?, ?, ?)",
        (saved_search["saved_search_id"], result.search_run_id, _now()),
    )
    conn.commit()

    thread = threading.Thread(
        target=_run_worker_background,
        args=(db_path, result.search_run_id, saved_search["candidate_id"]),
        daemon=True,
    )
    thread.start()

    return {"search_run_id": result.search_run_id, "status": "QUEUED", "already_running": False}


def get_run_sources(conn, search_run_id):
    """
    Per-source execution audit for one run (search_run_sources, Phase 1)
    -- attempted/reachable/succeeded/raw/eligible/displayed counts,
    status (NOT_ATTEMPTED/NOT_CONFIGURED/BLOCKED/FAILED/ZERO/SUCCESS),
    and error detail, one row per source that was actually part of the
    run's plan. `board` is the human-facing board name (e.g. "LINKEDIN"
    for registry key "LINKEDIN_SEARCH") -- derived via source_registry.
    board_name_for_source(), never a second stored/duplicated name.

    Defensive: returns [] rather than raising if search_run_sources
    doesn't exist yet on this DB (an older DB that hasn't run
    migrate_v5_search_run_sources.py) -- this is purely additive
    summary information, never required for the run/results endpoints
    that already worked before it existed.
    """
    try:
        rows = conn.execute(
            "SELECT * FROM search_run_sources WHERE search_run_id = ? ORDER BY source",
            (search_run_id,),
        ).fetchall()
    except Exception as error:
        if "no such table" in str(error):
            return []
        raise

    sources = []
    for row in rows:
        entry = dict(row)
        entry["board"] = source_registry.board_name_for_source(entry["source"])
        entry["attempted"] = bool(entry["attempted"])
        entry["reachable"] = bool(entry["reachable"]) if entry["reachable"] is not None else None
        entry["succeeded"] = bool(entry["succeeded"]) if entry["succeeded"] is not None else None
        details_raw = entry.get("details_json")
        try:
            entry["details"] = json.loads(details_raw) if details_raw else {}
        except json.JSONDecodeError:
            entry["details"] = {}
        sources.append(entry)
    return sources


def get_run_status(conn, search_run_id):
    row = conn.execute(
        "SELECT * FROM search_runs WHERE search_run_id = ?", (search_run_id,)
    ).fetchone()
    if row is None:
        raise SearchStoreError(f"Unknown run: {search_run_id!r}")
    result = dict(row)
    # Additive only -- every existing key/value above is untouched, so
    # no existing client of GET /api/runs/{id} can break.
    result["sources"] = get_run_sources(conn, search_run_id)
    return result


def get_run_status_for_candidate(conn, search_run_id, candidate_id):
    """Ownership-enforcing variant of get_run_status() -- see
    get_saved_search_for_candidate()'s docstring for the same rationale
    and the same deliberately-indistinguishable-from-unknown behavior."""
    result = get_run_status(conn, search_run_id)
    if result["candidate_id"] != candidate_id:
        raise SearchStoreError(f"Unknown run: {search_run_id!r}")
    return result


def get_latest_run_id_for_search(conn, saved_search_id):
    """
    The single canonical "most recently created run" for one saved
    search, or None if it has never been run -- used wherever a run's
    query-plan snapshot (valid the instant a run is created, before it
    necessarily finishes) is the right thing to read.

    For "which run's audit/results should a human be shown" (the
    results page, its source-audit table, and the per-search Excel
    export), use get_latest_usable_run_id_for_search() below instead --
    it selects the newest run that is NOT RUNNING/QUEUED AND has at
    least one real search_run_sources audit row (falling back to this
    function's own "newest run overall" behavior if no run meets both
    conditions), so a human view never shows an in-progress or
    orphaned-and-never-actually-ran run's empty audit next to an
    older, real, completed run's populated one.
    """
    run_ids = list_run_ids_for_search(conn, saved_search_id)
    return run_ids[-1] if run_ids else None


def get_latest_usable_run_id_for_search(conn, saved_search_id):
    """
    The most recent run that has actually REACHED some terminal state
    (COMPLETED/PARTIAL/FAILED/BLOCKED) -- i.e. the newest run that
    isn't currently RUNNING/QUEUED. Falls back to get_latest_run_id_
    for_search()'s "just the newest run" behavior when every run for
    this search is still RUNNING/QUEUED (e.g. its very first run is
    still in progress) -- so a brand-new search still shows that
    in-progress run rather than nothing.

    Deliberately does NOT prefer COMPLETED/PARTIAL over a genuinely-
    attempted FAILED/BLOCKED: a run that actually executed and finished
    BLOCKED (or FAILED) is real, honest truth about what just happened
    and must never be hidden behind an older, rosier SUCCESS -- see
    test_results_run_consistency.py's own "safety" checks, which this
    function must keep passing unchanged. The discriminator is
    therefore NOT status alone -- it's status AND whether the run ever
    recorded any real per-source audit data (search_run_sources rows).
    A run recovered by recover_orphaned_runs() above is FAILED but has
    ZERO search_run_sources rows (it never actually executed anything
    before its worker died) -- that kind of "FAILED" must NOT outrank
    an older run that genuinely ran and produced real results, or the
    exact bug this function fixes just reappears one status-value
    later. A run that genuinely ran and got BLOCKED/FAILED partway
    through DOES have real search_run_sources rows for whatever it did
    attempt, and correctly outranks an older SUCCESS.

    Root cause this fixes (found via a real end-to-end test): a second
    run created moments after a real completed one (e.g. an accidental
    duplicate "Run Now," or -- since this fix -- now prevented outright
    by trigger_run()'s concurrent-run guard) could become orphaned
    (its worker thread dies with an API restart and is never resumed --
    a RUNNING row left behind is NOT automatically reclaimed on its
    own; see scripts/search_worker.py's run_once() docstring, and
    recover_orphaned_runs() above for the startup-recovery half of this
    fix). Since it was chronologically newest, get_latest_run_id_for_
    search() would then point every source-audit/results-run-id
    display at that orphaned run's empty audit, right next to a real
    completed run's populated one -- exactly the reported "Results =
    <completed run> but Source Audit = <orphaned run>" bug.

    Distinct from get_latest_run_id_for_search(), which remains
    unchanged and is still the right choice wherever "this search's
    most recently created run" is the actual question being asked
    (e.g. resolving a query-plan snapshot, which is valid from the
    moment a run is created, before it necessarily finishes) -- this
    function is specifically for "which run's audit/results should a
    human be shown."
    """
    try:
        row = conn.execute(
            """
            SELECT sr.search_run_id
            FROM saved_search_runs ssr
            JOIN search_runs sr ON sr.search_run_id = ssr.search_run_id
            WHERE ssr.saved_search_id = ?
              AND sr.status NOT IN ('RUNNING', 'QUEUED')
              AND EXISTS (SELECT 1 FROM search_run_sources srs WHERE srs.search_run_id = sr.search_run_id)
            ORDER BY sr.created_at DESC
            LIMIT 1
            """,
            (saved_search_id,),
        ).fetchone()
    except Exception as error:
        if "no such table" not in str(error):
            raise
        # Defensive fallback for a DB predating migrate_v5_search_run_
        # sources.py (same posture as get_run_sources()/get_source_
        # filter_summary() elsewhere in this module).
        row = None
    if row:
        return row[0]
    return get_latest_run_id_for_search(conn, saved_search_id)


def get_source_filter_summary(conn, saved_search_id):
    """
    The COMPLETE source universe for one saved search's most recent
    run, for the results-page source filter (Phase 4) -- every source
    that was actually configured for this search, even one that
    returned zero jobs or was never attempted, never only the sources
    that happen to appear in the current result set.

    Built from two things, unioned:
      1. This saved search's own configured `sources` list (its
         sources_json column) -- the human's actual selection.
      2. The most recent run's search_run_sources audit rows (Phase 1),
         which is the ground truth for what was actually attempted.
    A configured source with no audit row yet (e.g. the search has
    never been run, or predates migrate_v5_search_run_sources.py) gets
    a synthesized NOT_ATTEMPTED placeholder so the filter is always
    complete -- never silently missing an entry.
    """
    saved_search = get_saved_search(conn, saved_search_id)
    configured_sources = saved_search.get("sources") or []

    latest_run_id = get_latest_usable_run_id_for_search(conn, saved_search_id)
    latest_sources_by_key = {}
    if latest_run_id:
        for entry in get_run_sources(conn, latest_run_id):
            latest_sources_by_key[entry["source"]] = entry

    all_keys = list(dict.fromkeys(list(configured_sources) + list(latest_sources_by_key.keys())))

    summary = []
    for key in all_keys:
        if key in latest_sources_by_key:
            summary.append(latest_sources_by_key[key])
        else:
            summary.append(
                {
                    "source": key,
                    "board": source_registry.board_name_for_source(key),
                    "attempted": False,
                    "status": "NOT_ATTEMPTED",
                    "raw_count": 0,
                    "eligible_count": 0,
                    "displayed_count": 0,
                    "reachable": None,
                    "succeeded": None,
                    "error_type": None,
                    "error_message": "This search has not been run yet.",
                    "duration_ms": None,
                }
            )
    return summary


def list_run_ids_for_search(conn, saved_search_id):
    rows = conn.execute(
        "SELECT search_run_id FROM saved_search_runs WHERE saved_search_id = ? ORDER BY created_at",
        (saved_search_id,),
    ).fetchall()
    return [r[0] for r in rows]
