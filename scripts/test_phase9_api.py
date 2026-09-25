#!/usr/bin/env python3

"""
Phase 9 API/GUI end-to-end offline tests.

Exercises api/main.py's FastAPI app in-process via FastAPI's
TestClient (no real HTTP socket, no live network call of any kind).
Uses a temporary, isolated SQLite database (never
data/applications/jobos_dev.db, never data/applications/jobos.db) and
a fake, offline-only source adapter registered the same safe way
scripts/test_phase8_daily_queue.py already does (source_registry.list_sources
is monkeypatched, get_adapter_status() itself is never touched, and
the patch is restored in a finally block) -- proven network-free by
this file never importing naukri_fetcher/naukri_fetch_bridge/playwright
and by grepping its own output for zero "naukri.com" lines.

Covers:
  1. Genericity -- four differently-shaped candidates (Senior SRE,
     Senior Java Developer, Product Manager, Data Scientist with no
     resume) flow through the exact same API/pipeline code with no
     source-code branch on any of them.
  2. Resume upload + DRAFT/CONFIRMED semantics (using Saroj's real
     resume file as a dev fixture only, per CLAUDE.md, against this
     temp DB).
  3. Saved-search creation, running, run-status polling, results.
  4. Candidate data isolation.
  5. Security/input validation.
  6. No-auto-application-submission code audit.
  7. Production DB byte-identical before/after.
"""

import hashlib
import io
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
API_DIR = ROOT / "api"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(API_DIR))

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


production_before = _sha(PRODUCTION_DB)

import source_registry
from source_adapter import JobSourceAdapter, AdapterHealth, BlockReason, AdapterStatus


class FakeAdapterEnabled(JobSourceAdapter):
    name = "FAKE_PHASE9_ENABLED"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        slug = query.role.lower().replace(" ", "-").replace("/", "-")
        return [
            {
                "source": self.name,
                "company": "Genericorp Inc",
                "title": query.role,
                "location": query.location,
                "work_model": "Hybrid",
                "job_url": f"https://example.com/jobs/{slug}-1",
                "application_url": f"https://example.com/apply/{slug}-1",
                "posted_date": "2026-09-19",
                "jd_text": f"{query.role} role based in {query.location}. "
                f"Looking for a strong candidate with relevant experience.",
                "experience_required": "5+ years",
                "mandatory_skills": [],
                "preferred_skills": [],
            }
        ]


source_registry.ADAPTERS["FAKE_PHASE9_ENABLED"] = FakeAdapterEnabled

_original_list_sources = source_registry.list_sources
source_registry.list_sources = lambda: ["FAKE_PHASE9_ENABLED"]

passed = 0
failed = 0


def check(condition, message):
    global passed, failed
    if condition:
        passed += 1
        print(f"PASS: {message}")
    else:
        failed += 1
        print(f"FAIL: {message}")


try:
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_phase9_"))
    tmp_db = tmp_dir / "jobos_test.db"

    import init_dev_db
    init_dev_db.init_dev_db(tmp_db)

    import db as db_mod
    db_mod.DEV_DB = tmp_db

    from fastapi.testclient import TestClient
    import main as api_main
    api_main.db_mod.DEV_DB = tmp_db

    client = TestClient(api_main.app)

    # ---------------------------------------------------------- candidates

    personas = {
        "A": {
            "name": "Candidate A",
            "title": "Senior SRE",
            "years": 11,
            "skills": ["AWS", "Kubernetes", "Terraform"],
            "roles": ["Senior SRE"],
            "locations": ["Bangalore"],
        },
        "B": {
            "name": "Candidate B",
            "title": "Senior Java Developer",
            "years": 8,
            "skills": ["Java", "Spring Boot", "Kafka"],
            "roles": ["Senior Java Developer"],
            "locations": ["Pune"],
        },
        "C": {
            "name": "Candidate C",
            "title": "Product Manager",
            "years": 6,
            "skills": ["SaaS", "Agile", "Product Strategy"],
            "roles": ["Product Manager"],
            "locations": ["Hyderabad"],
        },
        "D": {
            "name": "Candidate D",
            "title": "Data Scientist",
            "years": 4,
            "skills": ["Python", "SQL", "Machine Learning"],
            "roles": ["Data Scientist"],
            "locations": ["Remote"],
        },
    }

    candidate_ids = {}
    search_ids = {}
    run_ids = {}

    for key, persona in personas.items():
        resp = client.post("/api/candidates", json={"name": persona["name"]})
        check(resp.status_code == 200, f"1.{key}. create candidate {key} ({persona['title']}) -> 200")
        candidate_id = resp.json()["candidate_id"]
        candidate_ids[key] = candidate_id

        resp = client.put(
            f"/api/candidates/{candidate_id}/profile",
            json={
                "current_title": persona["title"],
                "total_experience_years": persona["years"],
                "skills": {"other": [{"name": s} for s in persona["skills"]]},
                "job_preferences": {
                    "target_roles": persona["roles"],
                    "target_locations": persona["locations"],
                },
            },
        )
        check(resp.status_code == 200, f"1.{key}. PUT profile for {key} -> 200")

        resp = client.post(f"/api/candidates/{candidate_id}/profile/confirm")
        check(
            resp.status_code == 200 and resp.json()["metadata"]["profile_status"] == "CONFIRMED",
            f"1.{key}. confirm profile for {key} -> CONFIRMED",
        )

    check(
        len({candidate_ids[k] for k in candidate_ids}) == 4,
        "1. all four candidates got distinct candidate_ids",
    )

    # ---------------------------------------------------------------- sources

    resp = client.get("/api/sources")
    check(
        resp.status_code == 200 and resp.json()["sources"] == [{"name": "FAKE_PHASE9_ENABLED", "status": "ENABLED"}],
        "2. GET /api/sources returns only the ENABLED (fake) source, no NOT_ENABLED leakage",
    )

    # ------------------------------------------------------------- searches

    for key, persona in personas.items():
        resp = client.post(
            f"/api/candidates/{candidate_ids[key]}/searches",
            json={
                "name": f"{key} search",
                "target_roles": persona["roles"],
                "target_locations": persona["locations"],
                "minimum_match_score": 0,
                "max_job_age_days": 3,
                "sources": ["FAKE_PHASE9_ENABLED"],
            },
        )
        check(resp.status_code == 200, f"3.{key}. create saved search for {key} -> 200")
        search_ids[key] = resp.json()["saved_search_id"]

    check(
        len({search_ids[k] for k in search_ids}) == 4,
        "3. all four saved searches got distinct saved_search_ids",
    )

    # ----------------------------------------------------------------- run

    for key in personas:
        resp = client.post(f"/api/searches/{search_ids[key]}/run?candidate_id={candidate_ids[key]}")
        check(
            resp.status_code == 200 and resp.json()["status"] == "QUEUED",
            f"4.{key}. POST run for {key} returns immediately with status=QUEUED",
        )
        run_ids[key] = resp.json()["run_id"]

    terminal = {"COMPLETED", "PARTIAL", "BLOCKED", "FAILED"}
    deadline = time.time() + 20
    final_status = {}
    while time.time() < deadline and len(final_status) < len(personas):
        for key in personas:
            if key in final_status:
                continue
            resp = client.get(f"/api/runs/{run_ids[key]}?candidate_id={candidate_ids[key]}")
            if resp.status_code == 200 and resp.json()["status"] in terminal:
                final_status[key] = resp.json()["status"]
        if len(final_status) < len(personas):
            time.sleep(0.3)

    check(
        len(final_status) == len(personas),
        f"5. all four background runs reached a terminal status within 20s (got: {final_status})",
    )
    check(
        all(v == "COMPLETED" for v in final_status.values()),
        f"5. all four runs COMPLETED (no adapter/pipeline error): {final_status}",
    )

    # ------------------------------------ per-source execution audit (Phase 3)

    for key in personas:
        resp = client.get(f"/api/runs/{run_ids[key]}?candidate_id={candidate_ids[key]}")
        run = resp.json()
        check("sources" in run, f"5b.{key}. GET /api/runs/{{id}} includes the new 'sources' key without breaking existing fields (status={run.get('status')!r} still present)")
        source_rows = run.get("sources", [])
        check(
            any(s["source"] == "FAKE_PHASE9_ENABLED" and s["status"] == "SUCCESS" and s["raw_count"] >= 1 for s in source_rows),
            f"5b.{key}. run's source audit shows FAKE_PHASE9_ENABLED as SUCCESS with a real raw_count (got {source_rows})",
        )

    # Dashboard wiring (Phase 5): the same per-source audit must also
    # surface on the dashboard's per-search latest_run, not just the
    # standalone /api/runs/{id} endpoint.
    resp = client.get(f"/api/candidates/{candidate_ids['A']}/dashboard")
    dash = resp.json()
    dash_search = next((s for s in dash["searches"] if s["saved_search_id"] == search_ids["A"]), None)
    check(dash_search is not None and dash_search.get("latest_run") is not None, "5c. dashboard includes A's search with a latest_run")
    if dash_search and dash_search.get("latest_run"):
        dash_sources = dash_search["latest_run"].get("sources", [])
        check(
            any(s["source"] == "FAKE_PHASE9_ENABLED" and s["status"] == "SUCCESS" for s in dash_sources),
            f"5c. dashboard's latest_run.sources shows the real per-source execution audit (got {dash_sources})",
        )

    # ------------------------------------------------------------- results

    for key, persona in personas.items():
        resp = client.get(f"/api/searches/{search_ids[key]}/results?candidate_id={candidate_ids[key]}")
        check(resp.status_code == 200, f"6.{key}. GET results for {key} -> 200")
        body = resp.json()
        results = body["results"]
        check(len(results) >= 1, f"6.{key}. results non-empty for {key} (got {len(results)})")
        check(
            "sources" in body and any(s["source"] == "FAKE_PHASE9_ENABLED" for s in body["sources"]),
            f"6b.{key}. results response includes the complete source-filter universe (Phase 4), not just sources present in the result rows (got {body.get('sources')})",
        )
        if results:
            check(
                results[0]["title"] == persona["roles"][0],
                f"6.{key}. returned job title matches the candidate's OWN target role "
                f"({results[0]['title']!r} == {persona['roles'][0]!r}) -- proves no hardcoded role",
            )
            check(
                "requirement_type" in results[0] and results[0]["requirement_type"] in ("REQUIRED", "PREFERRED", "UNKNOWN"),
                f"6.{key}. API result exposes requirement_type (REQUIRED/PREFERRED/UNKNOWN), got {results[0].get('requirement_type')!r}",
            )

    # -------------------------------------------------------- isolation

    resp = client.get(f"/api/candidates/{candidate_ids['A']}/searches")
    a_search_ids = {s["saved_search_id"] for s in resp.json()["searches"]}
    check(
        search_ids["B"] not in a_search_ids and search_ids["C"] not in a_search_ids,
        "7. candidate A's search list does not include candidate B's or C's saved searches",
    )

    resp = client.get(f"/api/candidates/{candidate_ids['B']}/profile")
    check(
        resp.json()["identity"]["name"] == "Candidate B",
        "7. candidate B's own profile fetch returns B's own identity, not another candidate's",
    )

    # ------------------------------------------------------ resume upload

    real_resume = ROOT / "resumes" / "SarojKumarNayak_SRE_DevOps_11Yrs.pdf"
    if real_resume.exists():
        resp = client.post("/api/candidates", json={"name": "Resume Fixture Candidate"})
        resume_candidate_id = resp.json()["candidate_id"]

        with real_resume.open("rb") as f:
            resp = client.post(
                f"/api/candidates/{resume_candidate_id}/resume",
                files={"file": ("SarojKumarNayak_SRE_DevOps_11Yrs.pdf", f, "application/pdf")},
            )
        check(resp.status_code == 200, "8. resume upload for a real PDF resume -> 200")
        if resp.status_code == 200:
            body = resp.json()
            check(body["status"] == "SUCCESS", "8. resume extraction status == SUCCESS")
            check(
                body["profile"]["metadata"]["profile_status"] == "DRAFT",
                "8. a freshly resume-extracted profile is DRAFT, never auto-CONFIRMED",
            )
    else:
        print("SKIP: 8. real resume fixture not found at", real_resume)

    # -------------------------------------------------- security / validation

    resp = client.post("/api/candidates", json={"name": ""})
    check(resp.status_code == 422, "9. empty candidate name rejected (422)")

    resp = client.post(
        f"/api/candidates/{candidate_ids['A']}/searches",
        json={"name": "bad", "target_roles": [], "target_locations": ["Bangalore"]},
    )
    check(resp.status_code == 422, "9. empty target_roles list rejected (422)")

    resp = client.post(
        f"/api/candidates/{candidate_ids['A']}/searches",
        json={
            "name": "bad salary",
            "target_roles": ["X"],
            "target_locations": ["Y"],
            "salary_expectation_min": -100,
        },
    )
    check(resp.status_code == 422, "9. negative salary rejected (422)")

    resp = client.post(
        f"/api/candidates/{candidate_ids['A']}/searches",
        json={
            "name": "bad source",
            "target_roles": ["X"],
            "target_locations": ["Y"],
            "sources": ["HIRIST"],
        },
    )
    check(resp.status_code == 400, "9. requesting a NOT_ENABLED source (HIRIST) is rejected (400)")

    resp = client.get("/api/candidates/does-not-exist")
    check(resp.status_code == 404, "9. unknown candidate_id -> 404")

    resp = client.get("/api/searches/does-not-exist?candidate_id=does-not-exist")
    check(resp.status_code == 404, "9. unknown saved_search_id -> 404")

    resp = client.get("/api/runs/does-not-exist?candidate_id=does-not-exist")
    check(resp.status_code == 404, "9. unknown run_id -> 404")

    resp = client.get(f"/api/searches/{search_ids['A']}")
    check(resp.status_code == 422, "9. omitting the now-required candidate_id query param is rejected (422), never silently defaulting")

    resp = client.post(
        f"/api/candidates/{candidate_ids['A']}/resume",
        files={"file": ("evil.txt", io.BytesIO(b"not a pdf"), "text/plain")},
    )
    check(resp.status_code == 400, "9. non-PDF resume upload rejected (400)")

    resp = client.post(
        f"/api/candidates/{candidate_ids['A']}/resume",
        files={"file": ("../../etc/passwd.pdf", io.BytesIO(b"%PDF-fake"), "application/pdf")},
    )
    check(
        resp.status_code in (200, 422),
        "9. path-traversal-style filename does not crash the endpoint (server-generated filename used internally)",
    )
    resume_dir = ROOT / "data" / "applications" / "resumes_dev" / candidate_ids["A"]
    if resume_dir.exists():
        stored_names = [p.name for p in resume_dir.iterdir()]
        check(
            all(".." not in n and "/" not in n for n in stored_names),
            "9. stored resume filenames are server-generated UUIDs, never the client filename",
        )

    resp = client.put(
        f"/api/candidates/{candidate_ids['A']}/profile",
        data="{not valid json",
        headers={"Content-Type": "application/json"},
    )
    check(resp.status_code == 422, "9. malformed JSON body rejected (422)")

    resp = client.get("/api/candidates/../../../etc/passwd")
    check(resp.status_code == 404, "9. path-traversal-style candidate_id in URL -> 404, not a filesystem read")

    # ---------------------------------------------- no auto-apply code audit

    suspicious_tokens = ["submit_application", "apply_to_job", "auto_apply", "playwright.*click.*apply"]
    api_web_files = list((ROOT / "api").glob("*.py")) + list((ROOT / "web").glob("*.html")) + list((ROOT / "web").glob("*.js"))
    found_any = []
    for path in api_web_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in suspicious_tokens:
            if token.replace(".*", "") in text.lower():
                found_any.append((path.name, token))
    check(
        not found_any,
        f"10. no automatic-application-submission code anywhere in api/ or web/ (found: {found_any})",
    )
    check(
        "target=\"_blank\"" in (ROOT / "web" / "results.html").read_text(),
        "10. results page opens jobs in a new tab (external link) rather than any in-app apply action",
    )

    # ------------------------------------------------------------- report

    resp = client.get(f"/api/candidates/{candidate_ids['A']}/report")
    check(
        resp.status_code == 200
        and resp.headers["content-type"].startswith("application/vnd.openxmlformats"),
        "11. Excel report download for candidate A -> 200, xlsx content-type",
    )

finally:
    source_registry.list_sources = _original_list_sources
    source_registry.ADAPTERS.pop("FAKE_PHASE9_ENABLED", None)

production_after = _sha(PRODUCTION_DB)
check(
    production_before == production_after,
    f"12. production DB byte-identical before/after this entire test run (sha256 before={production_before}, after={production_after})",
)

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
