#!/usr/bin/env python3

"""
Section 7 end-to-end acceptance: all 11 logical sources (NAUKRI, HIRIST,
IIMJOBS, APNA, LINKEDIN, INDEED, FOUNDIT, INSTAHYRE, CUTSHORT, WELLFOUND,
SHINE) flow through source selection -> query plan -> queue -> worker ->
normalization -> dedup -> scoring -> DB -> API -> Excel report, with
source, experience eligibility, requirement_type, and freshness surviving
every layer, and API/report agreeing.

SAFETY, matching the documented incident in test_phase8_daily_queue.py
and the convention in test_multi_source_result_distribution.py: never
relies on submit_search()'s sources=None default. Registers 11 new,
offline, synthetic adapters under new FAKE_11_* keys and always passes
`sources=` explicitly listing only those 11. No real adapter class is
ever instantiated or called -- no restricted-board (search-provider)
requests of any kind.

Never opens data/applications/jobos.db. Never makes a network call.
"""

import json
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "api"))

import init_tracker
import migrate_v2_schema
import migrate_v3_saved_searches
import migrate_v4_search_extensions
import source_registry
from source_adapter import JobSourceAdapter, AdapterHealth, BlockReason, AdapterStatus
from candidate_profile import normalize_candidate_profile, promote_to_confirmed, serialize_candidate_profile
from search_submission import submit_search
from search_worker import claim_next_queue_item, process_queue_item
import generate_run_report as grr

import results_store
import profile_store

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"

_RICH_JD = (
    "Senior Site Reliability Engineer / DevOps role. Own production reliability "
    "across AWS and Azure. Run Kubernetes (EKS/AKS) clusters, manage "
    "infrastructure as code with Terraform and Ansible, build CI/CD pipelines "
    "with Jenkins and GitHub Actions, and drive incident management, "
    "SLO/SLI/error budget practices, and root cause analysis. Observability "
    "via Prometheus and Grafana is core."
)

# The 11 logical sources this project targets, matching config/search_planner.json's
# direct_sources + search_provider_sources -- FAKE_11_ prefix, never the real registry keys.
LOGICAL_SOURCES = [
    "NAUKRI", "HIRIST", "IIMJOBS", "APNA",
    "LINKEDIN", "INDEED", "FOUNDIT", "INSTAHYRE", "CUTSHORT", "WELLFOUND", "SHINE",
]
FAKE_SOURCES = [f"FAKE_11_{name}" for name in LOGICAL_SOURCES]

# Cycle experience-requirement text across sources so REQUIRED/PREFERRED/
# UNKNOWN and a range of eligibility outcomes are all exercised.
_EXPERIENCE_TEXTS = [
    "8-12 years required",       # REQUIRED, eligible
    "8-12 years preferred",      # PREFERRED, eligible
    "8-12 years",                 # UNKNOWN, eligible
    "5+ years",                   # UNKNOWN, eligible
    "15+ years",                  # UNKNOWN, NOT eligible (ABOVE_PROFILE)
    "1-3 years",                  # UNKNOWN, NOT eligible (BELOW_PROFILE)
]

# Cycle posted_date offsets so freshness varies (HOT/FRESH/AGING/OLD/STALE/UNKNOWN).
_TODAY = date.today()
_FRESHNESS_DAYS_AGO = [0, 5, 10, 20, 45, None]  # None -> no posted_date at all (UNKNOWN)


def _posted_date_for(offset):
    if offset is None:
        return ""
    return (_TODAY - timedelta(days=offset)).isoformat()


def _make_fake_adapter(source_name, index):
    exp_text = _EXPERIENCE_TEXTS[index % len(_EXPERIENCE_TEXTS)]
    posted = _posted_date_for(_FRESHNESS_DAYS_AGO[index % len(_FRESHNESS_DAYS_AGO)])
    jobs = [
        {
            "source": source_name,
            "job_id": f"{source_name}-JOB-1",
            "company": f"{source_name} Corp",
            "title": "Senior Site Reliability Engineer",
            "location": "Bengaluru",
            "work_model": "Hybrid",
            "job_url": f"https://{source_name.lower()}.example.com/jobs/1",
            "application_url": f"https://{source_name.lower()}.example.com/apply/1",
            "posted_date": posted,
            "jd_text": _RICH_JD,
            "experience_required": exp_text,
            "mandatory_skills": [],
            "preferred_skills": [],
        },
    ]

    class _FakeAdapter(JobSourceAdapter):
        name = source_name
        status = AdapterStatus.ENABLED

        def health_check(self):
            return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

        def search(self, query):
            return list(jobs)

    _FakeAdapter.__name__ = f"FakeAdapter_{source_name}"
    return _FakeAdapter, exp_text, posted


_SOURCE_METADATA = {}
for _i, (_logical, _fake) in enumerate(zip(LOGICAL_SOURCES, FAKE_SOURCES)):
    _cls, _exp_text, _posted = _make_fake_adapter(_fake, _i)
    source_registry.ADAPTERS[_fake] = _cls
    _SOURCE_METADATA[_fake] = {"experience_required": _exp_text, "posted_date": _posted}

# A deliberate cross-source duplicate: FAKE_11_LINKEDIN and FAKE_11_INDEED
# both post the identical job (same title+company+JD) -- proves cross-
# source dedup detects it WITHOUT erasing either source's own row.
_DUP_TITLE = "Duplicate-Cluster Senior SRE"
_DUP_COMPANY = "Shared Duplicate Corp"


def _make_dup_adapter(source_name):
    job = {
        "source": source_name,
        "job_id": f"{source_name}-DUP-JOB",
        "company": _DUP_COMPANY,
        "title": _DUP_TITLE,
        "location": "Bengaluru",
        "work_model": "Hybrid",
        "job_url": f"https://{source_name.lower()}.example.com/jobs/dup",
        "application_url": f"https://{source_name.lower()}.example.com/apply/dup",
        "posted_date": _posted_date_for(0),
        "jd_text": _RICH_JD,
        "experience_required": "8-12 years",
        "mandatory_skills": [],
        "preferred_skills": [],
    }
    existing_cls = source_registry.ADAPTERS[source_name]

    class _DupAdapter(existing_cls):
        def search(self, query):
            return super().search(query) + [job]

    _DupAdapter.__name__ = f"DupAdapter_{source_name}"
    return _DupAdapter


source_registry.ADAPTERS["FAKE_11_LINKEDIN"] = _make_dup_adapter("FAKE_11_LINKEDIN")
source_registry.ADAPTERS["FAKE_11_INDEED"] = _make_dup_adapter("FAKE_11_INDEED")


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _sha(path):
    import hashlib
    if not Path(path).exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _new_isolated_db():
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_11source_"))
    tmp_db = tmp_dir / "jobos_test.db"
    init_tracker.DATA_DIR = tmp_dir
    init_tracker.DB_PATH = tmp_db
    init_tracker.main()

    conn = sqlite3.connect(tmp_db)
    conn.execute("PRAGMA foreign_keys = ON")
    migrate_v2_schema._create_new_tables(conn)
    migrate_v2_schema._ensure_job_columns(conn)
    conn.commit()
    conn.close()

    migrate_v3_saved_searches.migrate(tmp_db)
    migrate_v4_search_extensions.migrate(tmp_db)

    return tmp_db


def _seed_confirmed_candidate(db_path, candidate_id):
    now = "2026-09-20T00:00:00+00:00"
    raw = {
        "identity": {"candidate_id": candidate_id, "name": "Test Candidate"},
        "professional_summary": {"total_experience_years": 11.0},
        "skills": {
            "cloud": [{"name": "AWS"}, {"name": "Azure"}],
            "containers_orchestration": [{"name": "Kubernetes"}, {"name": "EKS"}, {"name": "AKS"}],
            "infrastructure_iac": [{"name": "Terraform"}, {"name": "Ansible"}],
            "cicd": [{"name": "Jenkins"}, {"name": "GitHub Actions"}],
            "observability": [{"name": "Prometheus"}, {"name": "Grafana"}],
        },
        "job_preferences": {
            "target_roles": ["Senior SRE"],
            "target_locations": ["Bengaluru"],
        },
    }
    profile = promote_to_confirmed(normalize_candidate_profile(raw))

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO candidates (candidate_id, name, email, phone, created_at, updated_at, status) VALUES (?,?,?,?,?,?,?)",
        (candidate_id, "Test Candidate", None, None, now, now, "ACTIVE"),
    )
    conn.execute(
        "INSERT INTO candidate_search_profile (candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) VALUES (?,1,'PROFILE',?,1,1,?,?)",
        (candidate_id, json.dumps(serialize_candidate_profile(profile)), now, now),
    )
    conn.commit()
    conn.close()


def _link_saved_search(db_path, candidate_id, search_run_id):
    now = "2026-09-20T00:00:00+00:00"
    saved_search_id = "search_test_11source"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        INSERT INTO saved_searches (
            saved_search_id, candidate_id, name,
            target_roles_json, target_locations_json, sources_json,
            status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
        """,
        (
            saved_search_id, candidate_id, "11-source acceptance test",
            json.dumps(["Senior SRE"]), json.dumps(["Bengaluru"]),
            json.dumps(FAKE_SOURCES), now, now,
        ),
    )
    conn.execute(
        "INSERT INTO saved_search_runs (saved_search_id, search_run_id, created_at) VALUES (?, ?, ?)",
        (saved_search_id, search_run_id, now),
    )
    conn.commit()
    conn.close()
    return saved_search_id


def main():
    failures = []
    print("11-SOURCE END-TO-END ACCEPTANCE TEST (section 7)")
    print("=================================================================================")

    production_before = _sha(PRODUCTION_DB)

    db = _new_isolated_db()
    candidate_id = "cand-11source"
    _seed_confirmed_candidate(db, candidate_id)

    submission = submit_search(
        db_path=str(db),
        candidate_id=candidate_id,
        sources=FAKE_SOURCES,  # NEVER sources=None
        max_job_age_days=None,
        dry_run=False,
    )
    if not submission.submitted:
        _fail(failures, f"submission did not submit: {submission}")

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    claimed = claim_next_queue_item(conn, candidate_id=candidate_id)
    if claimed is None:
        _fail(failures, "expected a claimable queue item, got None")
        conn.close()
        print("\nFAILURES:\n" + "\n".join(f"  {f}" for f in failures))
        sys.exit(1)

    work_result = process_queue_item(conn, claimed)
    conn.close()

    if work_result.status != "COMPLETED":
        _fail(failures, f"expected queue item to COMPLETE, got status={work_result.status} unimplemented_sources={work_result.unimplemented_sources}")
    else:
        print(f"PASS: 1. queue item COMPLETED across all 11 fake sources (raw_count={work_result.raw_count}, matches_created={work_result.matches_created})")

    # --- 2. DB layer: all 11 sources present in jobs + candidate_job_matches ---
    conn2 = sqlite3.connect(db)
    conn2.row_factory = sqlite3.Row
    conn2.execute("PRAGMA foreign_keys = ON")
    placeholders = ",".join("?" for _ in FAKE_SOURCES)
    job_sources = {row[0] for row in conn2.execute(
        f"SELECT DISTINCT source FROM jobs WHERE source IN ({placeholders})", FAKE_SOURCES
    )}
    match_sources = {row[0] for row in conn2.execute(
        f"""
        SELECT DISTINCT j.source FROM candidate_job_matches m
        JOIN jobs j ON j.job_id = m.job_id
        WHERE m.candidate_id = ? AND j.source IN ({placeholders})
        """,
        [candidate_id, *FAKE_SOURCES],
    )}

    if job_sources != set(FAKE_SOURCES):
        _fail(failures, f"2. jobs table: expected all 11 sources, missing {set(FAKE_SOURCES) - job_sources} -- results are NOT APNA-only-shaped, but source coverage is incomplete")
    else:
        print(f"PASS: 2. jobs table contains rows from all 11 sources (not APNA-only, not any single source)")

    if match_sources != set(FAKE_SOURCES):
        _fail(failures, f"2. candidate_job_matches: expected all 11 sources, missing {set(FAKE_SOURCES) - match_sources}")
    else:
        print("PASS: 2. candidate_job_matches contains rows from all 11 sources")

    # --- 3. dedup: the deliberate LINKEDIN/INDEED duplicate cluster --
    #     both source rows must still exist, neither silently erased ---
    dup_rows = conn2.execute(
        "SELECT source, job_id FROM jobs WHERE title = ? AND company = ?",
        (_DUP_TITLE, _DUP_COMPANY),
    ).fetchall()
    dup_sources_present = {row["source"] for row in dup_rows}
    if dup_sources_present != {"FAKE_11_LINKEDIN", "FAKE_11_INDEED"}:
        _fail(failures, f"3. expected the duplicate cluster to retain BOTH source rows (FAKE_11_LINKEDIN, FAKE_11_INDEED), got {dup_sources_present}")
    else:
        print("PASS: 3. cross-source duplicate cluster retains BOTH sources' own job rows (dedup flags, never erases)")

    conn2.close()

    # --- 4. API layer: source, eligibility, requirement_type, freshness all survive ---
    saved_search_id = _link_saved_search(db, candidate_id, submission.search_run_id)
    conn3 = sqlite3.connect(db)
    conn3.row_factory = sqlite3.Row
    conn3.execute("PRAGMA foreign_keys = ON")
    try:
        api_results = results_store.get_results_for_saved_search(conn3, candidate_id, saved_search_id)
    finally:
        conn3.close()

    # --- 3b. dedup at the API layer: BOTH cluster members remain visible
    #     in the general results listing (only APPLY_TODAY suppresses the
    #     non-representative one) -- source info is flagged, never deleted ---
    dup_api_rows = [r for r in api_results if r["title"] == _DUP_TITLE and r["company"] == _DUP_COMPANY]
    dup_api_sources = {r["source"] for r in dup_api_rows}
    if dup_api_sources != {"FAKE_11_LINKEDIN", "FAKE_11_INDEED"}:
        _fail(failures, f"3b. expected BOTH duplicate-cluster sources to remain visible in the general results listing, got {dup_api_sources}")
    else:
        suppressed_rows = [r for r in dup_api_rows if r["duplicate_suppressed"]]
        if len(suppressed_rows) != 1:
            _fail(failures, f"3b. expected exactly 1 of the 2 cluster members flagged duplicate_suppressed (the non-representative one), got {len(suppressed_rows)}")
        else:
            other_sources = {d["other_source"] for d in (suppressed_rows[0].get("duplicate_candidates") or [])}
            if not other_sources & {"FAKE_11_LINKEDIN", "FAKE_11_INDEED"}:
                _fail(failures, f"3b. the suppressed row must expose the OTHER source's identity via duplicate_candidates, got {suppressed_rows[0].get('duplicate_candidates')}")
            else:
                print(f"PASS: 3b. both duplicate-cluster sources remain visible in results; the suppressed one preserves the other's source/URL via duplicate_candidates (never silently deleted): {suppressed_rows[0].get('duplicate_candidates')}")

    api_sources = {r["source"] for r in api_results}
    if api_sources != set(FAKE_SOURCES):
        _fail(failures, f"4. API results: expected all 11 sources, missing {set(FAKE_SOURCES) - api_sources} -- results are APNA-only-shaped or otherwise incomplete")
    else:
        print("PASS: 4. API results (the same function /api/searches/{id}/results calls) span all 11 sources")

    missing_requirement_type = [r["source"] for r in api_results if "requirement_type" not in r or r["requirement_type"] not in ("REQUIRED", "PREFERRED", "UNKNOWN")]
    if missing_requirement_type:
        _fail(failures, f"4. requirement_type missing/invalid on API results for: {missing_requirement_type}")
    else:
        print("PASS: 4. requirement_type present and valid on every API result across all 11 sources")

    requirement_types_seen = {r["requirement_type"] for r in api_results}
    if not {"REQUIRED", "PREFERRED"}.issubset(requirement_types_seen):
        _fail(failures, f"4. expected both REQUIRED and PREFERRED to actually appear (not just UNKNOWN) across the 11 sources, got {requirement_types_seen}")
    else:
        print(f"PASS: 4. requirement_type genuinely varies across sources: {sorted(requirement_types_seen)}")

    freshness_values_seen = {r["freshness"] for r in api_results}
    if len(freshness_values_seen) < 3:
        _fail(failures, f"4. expected freshness to genuinely vary across the 11 sources' different posted_dates, got only {freshness_values_seen}")
    else:
        print(f"PASS: 4. freshness varies across sources as expected: {sorted(freshness_values_seen)}")

    missing_eligible_field = [r["source"] for r in api_results if "eligible" not in r or "eligibility" not in r]
    if missing_eligible_field:
        _fail(failures, f"4. eligibility fields missing on API results for: {missing_eligible_field}")
    elif not all(r["eligible"] for r in api_results):
        _fail(failures, "4. candidate_job_matches/results only ever holds ELIGIBLE jobs by design -- an ineligible one leaked through")
    else:
        print("PASS: 4. every API result is eligible (candidate_job_matches only ever holds eligible jobs by design)")

    # Experience eligibility genuinely varying is proven at the WORKER
    # layer instead: candidate_job_matches never holds an ineligible job
    # by design (see search_worker.upsert_candidate_job_match()'s own
    # docstring), so ineligible jobs (this fixture's "1-3 years" and
    # "15+ years" sources) correctly never reach the API/report at all
    # -- proven by the worker's own exclusion count, not by their
    # absence from results (which would be indistinguishable from a bug).
    if work_result.excluded_experience < 1:
        _fail(failures, f"4. expected at least 1 job excluded on experience grounds (this fixture includes '1-3 years' and '15+ years' sources), got excluded_experience={work_result.excluded_experience}")
    else:
        print(f"PASS: 4. experience eligibility genuinely varied upstream -- {work_result.excluded_experience} job(s) correctly excluded before ever reaching candidate_job_matches/API")

    # --- 5. Excel/report layer: agrees with the API on source set + per-job eligibility ---
    conn4 = sqlite3.connect(db)
    jobs_for_report = grr.load_jobs(conn4)
    matches_for_report = grr.load_candidate_job_matches(conn4, candidate_id)
    conn4.close()

    from candidate_profile import to_legacy_matching_profile
    # Reload the confirmed profile the same way the API does, for build_report_rows().
    conn5 = sqlite3.connect(db)
    conn5.row_factory = sqlite3.Row
    confirmed_profile = profile_store.load_active_profile(conn5, candidate_id)
    conn5.close()
    legacy_profile = to_legacy_matching_profile(confirmed_profile)

    report_rows, _dup_candidates = grr.build_report_rows(jobs_for_report, legacy_profile, candidate_job_matches=matches_for_report)
    report_rows_11 = [r for r in report_rows if r.ranking.source in FAKE_SOURCES]
    report_sources = {r.ranking.source for r in report_rows_11}

    if report_sources != set(FAKE_SOURCES):
        _fail(failures, f"5. Excel report rows: expected all 11 sources, missing {set(FAKE_SOURCES) - report_sources}")
    else:
        print("PASS: 5. Excel report (generate_run_report.build_report_rows, the same function the download endpoint uses) spans all 11 sources")

    api_by_source_job = {(r["source"], r["job_id"]): r for r in api_results}
    report_by_source_job = {(r.ranking.source, r.ranking.job_id): r for r in report_rows_11}
    disagreements = []
    for key, api_row in api_by_source_job.items():
        report_row = report_by_source_job.get(key)
        if report_row is None:
            disagreements.append(f"{key}: present in API, missing from report")
            continue
        if api_row["eligible"] != report_row.ranking.eligible:
            disagreements.append(f"{key}: eligible API={api_row['eligible']} report={report_row.ranking.eligible}")
        if api_row["source"] != report_row.ranking.source:
            disagreements.append(f"{key}: source API={api_row['source']} report={report_row.ranking.source}")
        if api_row["requirement_type"] != report_row.ranking.requirement_type:
            disagreements.append(f"{key}: requirement_type API={api_row['requirement_type']} report={report_row.ranking.requirement_type}")
        if api_row["freshness"] != report_row.ranking.freshness:
            disagreements.append(f"{key}: freshness API={api_row['freshness']} report={report_row.ranking.freshness}")

    if disagreements:
        for d in disagreements:
            _fail(failures, f"5. API/report disagreement -- {d}")
    else:
        print(f"PASS: 5. API and Excel report AGREE on source, eligibility, requirement_type, and freshness for every one of {len(api_by_source_job)} jobs across all 11 sources")

    production_after = _sha(PRODUCTION_DB)
    if production_before != production_after:
        _fail(failures, "production DB was touched (SHA changed) -- SAFETY VIOLATION")
    else:
        print("PASS: production DB untouched (SHA unchanged)")

    print()
    if failures:
        print(f"TOTAL: {len(failures)} failure(s)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)

    print("ALL 11-SOURCE ACCEPTANCE CHECKS PASSED")


if __name__ == "__main__":
    main()
