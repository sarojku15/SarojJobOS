#!/usr/bin/env python3

"""
Offline, controlled fixture proving the full pipeline -- source
selection -> query plan -> queue -> worker -> DB -> saved-search
results (the same call /api/searches/{id}/results makes) -- treats
every currently-ENABLED source symmetrically and never collapses to a
single source ("the APNA-only results bug" investigation).

SAFETY, matching the documented incident in test_phase8_daily_queue.py
(an earlier test that omitted --source and made 14 unauthorized live
requests to naukri.com): this file NEVER relies on submit_search()'s
sources=None default (which resolves to the REAL process-wide
source_registry.ADAPTERS). It registers four new, offline, synthetic
adapters under new keys and always passes `sources=` explicitly
listing only those four. The real NAUKRI/HIRIST/IIMJOBS/APNA adapter
classes are never instantiated or called by this file.

Never opens data/applications/jobos.db. Never makes a network call.
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "api"))

import init_tracker
import migrate_v2_schema
import migrate_v3_saved_searches
import migrate_v4_search_extensions
import source_registry
from source_adapter import JobSourceAdapter, AdapterHealth, BlockReason, AdapterStatus, SearchQuery
from candidate_profile import normalize_candidate_profile, promote_to_confirmed, serialize_candidate_profile
from search_submission import submit_search
from search_worker import claim_next_queue_item, process_queue_item

import results_store

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"

_RICH_JD = (
    "Senior Site Reliability Engineer / DevOps role. Own production reliability "
    "across AWS and Azure. Run Kubernetes (EKS/AKS) clusters, manage "
    "infrastructure as code with Terraform and Ansible, build CI/CD pipelines "
    "with Jenkins and GitHub Actions, and drive incident management, "
    "SLO/SLI/error budget practices, and root cause analysis. Observability "
    "via Prometheus and Grafana is core."
)

# Four synthetic, offline stand-ins for the four real currently-ENABLED
# direct sources (NAUKRI, HIRIST, IIMJOBS, APNA) -- new registry keys,
# never the real ones, per the safety note above.
FAKE_SOURCES = ["FAKE_DIST_NAUKRI", "FAKE_DIST_HIRIST", "FAKE_DIST_IIMJOBS", "FAKE_DIST_APNA"]


def _make_fake_adapter(source_name, job_count=2):
    jobs = [
        {
            "source": source_name,
            "job_id": f"{source_name}-JOB-{i}",
            "company": f"{source_name} Corp {i}",
            "title": "Senior Site Reliability Engineer",
            "location": "Bengaluru",
            "work_model": "Hybrid",
            "job_url": f"https://{source_name.lower()}.example.com/jobs/{i}",
            "application_url": f"https://{source_name.lower()}.example.com/apply/{i}",
            "posted_date": "2026-09-20",
            "jd_text": _RICH_JD,
            "experience_required": "8-12 years",
            "mandatory_skills": [],
            "preferred_skills": [],
        }
        for i in range(1, job_count + 1)
    ]

    class _FakeAdapter(JobSourceAdapter):
        name = source_name
        status = AdapterStatus.ENABLED

        def health_check(self):
            return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

        def search(self, query):
            return list(jobs)

    _FakeAdapter.__name__ = f"FakeAdapter_{source_name}"
    return _FakeAdapter


for _source_name in FAKE_SOURCES:
    source_registry.ADAPTERS[_source_name] = _make_fake_adapter(_source_name)


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _new_isolated_db():
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_multisource_dist_"))
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
        "professional_summary": {"total_experience_years": 11},
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
    """Minimal, direct insert into saved_searches/saved_search_runs --
    bypasses the API layer's pydantic payload construction (already
    exercised by other tests) so this file can focus on proving the
    underlying multi-source pipeline itself, using the exact same
    tables/query results_store.get_results_for_saved_search() reads
    (the same function /api/searches/{id}/results calls)."""
    now = "2026-09-20T00:00:00+00:00"
    saved_search_id = "search_test_dist"
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
            saved_search_id, candidate_id, "Distribution test",
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
    print("MULTI-SOURCE RESULT DISTRIBUTION TEST (APNA-only bug investigation, section H)")
    print("=================================================================================")

    production_before = _sha(PRODUCTION_DB) if PRODUCTION_DB.exists() else None

    db = _new_isolated_db()
    candidate_id = "cand-dist"
    _seed_confirmed_candidate(db, candidate_id)

    submission = submit_search(
        db_path=str(db),
        candidate_id=candidate_id,
        sources=FAKE_SOURCES,  # NEVER sources=None -- see module docstring
        max_job_age_days=None,
        dry_run=False,
    )
    if not submission.submitted:
        _fail(failures, f"submit_search did not submit: {submission}")
    else:
        print(f"PASS: submission created search_run_id={submission.search_run_id} for sources={submission.sources}")

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    claimed = claim_next_queue_item(conn, candidate_id=candidate_id)
    if claimed is None:
        _fail(failures, "expected a claimable queue item, got None")
        conn.close()
    else:
        result = process_queue_item(conn, claimed)
        conn.close()

        if result.status != "COMPLETED":
            _fail(failures, f"expected queue item to COMPLETE, got status={result.status} unimplemented_sources={result.unimplemented_sources}")
        else:
            print(f"PASS: queue item COMPLETED, raw_count={result.raw_count} matches_created={result.matches_created}")

        # --- DB layer: jobs table grouped by source ---
        conn2 = sqlite3.connect(db)
        job_sources = {row[0] for row in conn2.execute(
            "SELECT DISTINCT source FROM jobs WHERE source IN ({})".format(
                ",".join("?" for _ in FAKE_SOURCES)
            ), FAKE_SOURCES
        )}
        match_sources = {row[0] for row in conn2.execute(
            """
            SELECT DISTINCT j.source FROM candidate_job_matches m
            JOIN jobs j ON j.job_id = m.job_id
            WHERE m.candidate_id = ? AND j.source IN ({})
            """.format(",".join("?" for _ in FAKE_SOURCES)),
            [candidate_id, *FAKE_SOURCES],
        )}
        conn2.close()

        if job_sources != set(FAKE_SOURCES):
            _fail(failures, f"jobs table: expected all 4 sources {set(FAKE_SOURCES)}, got {job_sources} -- NOT source-symmetric (APNA-only-shaped bug)")
        else:
            print(f"PASS: jobs table contains rows from all 4 sources: {sorted(job_sources)}")

        if match_sources != set(FAKE_SOURCES):
            _fail(failures, f"candidate_job_matches: expected all 4 sources {set(FAKE_SOURCES)}, got {match_sources} -- NOT source-symmetric (APNA-only-shaped bug)")
        else:
            print(f"PASS: candidate_job_matches contains rows from all 4 sources: {sorted(match_sources)}")

        # --- API layer: the exact function /api/searches/{id}/results calls ---
        saved_search_id = _link_saved_search(db, candidate_id, submission.search_run_id)
        conn3 = sqlite3.connect(db)
        conn3.row_factory = sqlite3.Row  # matches api/db.py's get_conn()
        conn3.execute("PRAGMA foreign_keys = ON")
        try:
            api_results = results_store.get_results_for_saved_search(conn3, candidate_id, saved_search_id)
        finally:
            conn3.close()

        api_sources = {r["source"] for r in api_results}
        if api_sources != set(FAKE_SOURCES):
            _fail(failures, f"API results: expected all 4 sources {set(FAKE_SOURCES)}, got {api_sources} -- NOT source-symmetric (APNA-only-shaped bug)")
        else:
            print(f"PASS: /api/searches/{{id}}/results-equivalent call returns jobs from all 4 sources: {sorted(api_sources)}")

    production_after = _sha(PRODUCTION_DB) if PRODUCTION_DB.exists() else None
    if production_before != production_after:
        _fail(failures, "production DB was touched (SHA changed) -- SAFETY VIOLATION")
    else:
        print("PASS: production DB untouched (SHA unchanged)")

    print()
    print(f"TOTAL: {len(failures)} failure(s)" if failures else "ALL CHECKS PASSED")
    for f in failures:
        print(f"  - {f}")

    return 1 if failures else 0


def _sha(path):
    import hashlib
    if not Path(path).exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    sys.exit(main())
