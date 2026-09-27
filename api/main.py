"""
Phase 9 API + local web app entry point.

Run with:
    .venv/bin/uvicorn api.main:app --reload --port 8420

Serves the JSON API under /api/... and the small vanilla-JS frontend
(web/) at the human-facing routes. Always talks to
data/applications/jobos_dev.db (see api/db.py) -- never production.
"""

import json
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
WEB_DIR = ROOT / "web"
RESUME_DIR = ROOT / "data" / "applications" / "resumes_dev"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

API_DIR = ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

# Phase 14.3: load .env into this process's environment at startup,
# standard dotenv semantics (setdefault -- never overrides a variable
# the real shell environment already set). Without this, a key saved
# via the Settings page (which correctly writes .env AND updates
# os.environ for the process handling that one request) would appear
# to "disappear" the next time this dev server restarts for any reason
# (uvicorn --reload restarts on every .py file change, not just when
# search_provider.py itself changes) -- .env itself was never wrong,
# nothing was ever loading it back into a fresh process. This now also
# happens automatically at search_provider.py's own import time (see
# that module's top-of-file note -- the root-cause fix covering every
# entrypoint, not just this one); this explicit call is kept, harmless
# and idempotent, as defense-in-depth for this process regardless of
# import order below.
import env_config as _env_config

_env_config.load_env_file_into_environ()

import db as db_mod
import profile_store
import results_store
import search_store
import search_worker
import manual_import
import application_lifecycle
import resume_store
import resume_variant_selector
import search_provider_settings_api as sp_settings_api
import resume_tailoring
import company_research
import interview_prep
import scheduler
from schemas import (
    CandidateCreate,
    CandidatePatch,
    CompanyResearchIn,
    FollowUpDateIn,
    InterviewAnswerIn,
    InterviewOutcomeIn,
    InterviewPrepIn,
    JobStatusUpdate,
    ManualJobImportIn,
    ProfileUpdate,
    SavedSearchCreate,
    SavedSearchUpdate,
    ScheduleSetIn,
    SearchProviderKeyIn,
    SearchProviderOrderIn,
    SearchProviderResetUsageIn,
    TailorResumeIn,
)

from candidate_profile import serialize_candidate_profile, validate_candidate_profile
from resume_extractor import extract_candidate_profile_draft_result

app = FastAPI(title="SarojJobOS Job Search")

MAX_RESUME_BYTES = 8 * 1024 * 1024  # 8MB


@app.on_event("startup")
def _recover_orphaned_runs_on_startup():
    """
    See api/search_store.recover_orphaned_runs()'s own docstring for
    why this is safe unconditionally in this project's architecture
    (no worker can survive a process restart, so anything still
    QUEUED/RUNNING at a fresh process's own startup is provably
    orphaned, never a false positive). Found via a real end-to-end
    test: a run left behind by a killed/restarted API process stayed
    RUNNING forever with a stale, empty audit, and (before this fix)
    could even outrank a real completed run as "the latest run" shown
    on the results/dashboard/export pages -- see
    get_latest_usable_run_id_for_search()'s docstring for the other,
    display-side half of this same fix.
    """
    conn = db_mod.get_conn()
    try:
        recovered = search_store.recover_orphaned_runs(conn)
        if recovered:
            print(f"[startup] Recovered {recovered} orphaned run(s) left RUNNING/QUEUED by a previous process.")
    finally:
        conn.close()


@app.middleware("http")
async def _no_cache_for_static_assets(request, call_next):
    """
    Root-cause fix for a real bug: static assets (web/*.js, *.css) and
    the *.html pages served via FileResponse below were served with NO
    Cache-Control header at all, so a browser's own default heuristic
    caching could serve a STALE cached /static/app.js on an ordinary
    refresh even after the file on disk changed (observed live:
    "SOURCE_STATUS_LABELS is not defined" after a page/script split
    that moved a shared const into app.js -- the served HTML was fresh,
    the cached JS was not). `no-cache` does NOT mean "never cache" --
    it means the browser must always revalidate via the existing
    ETag/Last-Modified (already sent by StaticFiles/FileResponse)
    before reusing a cached copy, so an unchanged file still gets a
    cheap 304 and a changed one is always served fresh. Scoped to
    static/html-serving paths only -- JSON API responses are
    unaffected.
    """
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if content_type.startswith(("text/html", "text/javascript", "text/css", "application/javascript")):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _profile_response(profile, validation=None):
    body = serialize_candidate_profile(profile)
    if validation is not None:
        body["_validation"] = {
            "valid": validation.valid,
            "errors": [
                {"field": i.field, "message": i.message, "severity": i.severity}
                for i in validation.errors
            ],
        }
    return body


# ---------------------------------------------------------------------- misc

@app.get("/api/health")
def health():
    return {"status": "OK", "db": "jobos_dev.db"}


# ---------------------------------------------------------------- candidates

@app.post("/api/candidates")
def create_candidate(payload: CandidateCreate):
    conn = db_mod.get_conn()
    try:
        candidate_id = profile_store.create_candidate(conn, payload.name, payload.email, payload.phone)
        return {"candidate_id": candidate_id}
    finally:
        conn.close()


@app.get("/api/candidates/{candidate_id}")
def get_candidate(candidate_id: str):
    conn = db_mod.get_conn()
    try:
        try:
            return profile_store.get_candidate(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))
    finally:
        conn.close()


@app.patch("/api/candidates/{candidate_id}")
def patch_candidate(candidate_id: str, patch: CandidatePatch):
    conn = db_mod.get_conn()
    try:
        try:
            profile_store.get_candidate(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))

        fields = {"name": patch.name, "email": patch.email, "phone": patch.phone}
        set_clauses = [f"{k} = ?" for k, v in fields.items() if v is not None]
        if set_clauses:
            params = [v for v in fields.values() if v is not None]
            params.append(profile_store._now())
            params.append(candidate_id)
            conn.execute(
                f"UPDATE candidates SET {', '.join(set_clauses)}, updated_at = ? WHERE candidate_id = ?",
                params,
            )
            conn.commit()
        return profile_store.get_candidate(conn, candidate_id)
    finally:
        conn.close()


# ------------------------------------------------------------------ profile

@app.get("/api/candidates/{candidate_id}/profile")
def get_profile(candidate_id: str):
    conn = db_mod.get_conn()
    try:
        try:
            profile = profile_store.load_active_profile(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))
        validation = validate_candidate_profile(profile)
        return _profile_response(profile, validation)
    finally:
        conn.close()


@app.put("/api/candidates/{candidate_id}/profile")
def update_profile(candidate_id: str, update: ProfileUpdate):
    conn = db_mod.get_conn()
    try:
        try:
            current = profile_store.load_active_profile(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))

        updated = profile_store.apply_profile_update(current, update)
        # A manual edit is not a new resume upload -- it still traces
        # back to whichever resume (if any) the profile being edited
        # already carried, never dropped or reset by an edit.
        source_resume_id = profile_store._current_source_resume_id(conn, candidate_id)
        profile_store.save_profile_as_new_version(conn, candidate_id, updated, source_resume_id=source_resume_id)
        validation = validate_candidate_profile(updated)
        return _profile_response(updated, validation)
    finally:
        conn.close()


@app.post("/api/candidates/{candidate_id}/profile/confirm")
def confirm_profile(candidate_id: str):
    conn = db_mod.get_conn()
    try:
        try:
            confirmed = profile_store.confirm_active_profile(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))
        except ValueError as error:
            raise HTTPException(422, str(error))
        return _profile_response(confirmed)
    finally:
        conn.close()


@app.post("/api/candidates/{candidate_id}/resume")
async def upload_resume(candidate_id: str, file: UploadFile = File(...)):
    conn = db_mod.get_conn()
    try:
        try:
            profile_store.get_candidate(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))

        filename = file.filename or ""
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(400, "Only .pdf resumes are supported")

        contents = await file.read()
        if len(contents) == 0:
            raise HTTPException(400, "Uploaded file is empty")
        if len(contents) > MAX_RESUME_BYTES:
            raise HTTPException(400, f"File exceeds the {MAX_RESUME_BYTES} byte limit")
        if contents[:5] != b"%PDF-":
            raise HTTPException(400, "File does not look like a valid PDF")

        candidate_dir = RESUME_DIR / candidate_id
        candidate_dir.mkdir(parents=True, exist_ok=True)
        # Server-generated filename only -- the client's own filename is
        # never used to build a filesystem path (no path traversal
        # surface of any kind).
        stored_name = f"{uuid.uuid4().hex}.pdf"
        stored_path = candidate_dir / stored_name
        stored_path.write_bytes(contents)

        result = extract_candidate_profile_draft_result(stored_path, candidate_id)

        # Records the resume FILE itself (item 9's genuinely missing
        # piece -- see resume_store.py's own docstring): a real,
        # candidate-provided fact (this exact file was uploaded, and
        # whether it parsed), independent of whether extraction
        # succeeded below. Never overwrites/deletes an earlier resume
        # row -- a distinct upload (different bytes) is always a new
        # row (UNIQUE(candidate_id, content_hash)), never silently
        # replacing prior application history that referenced an
        # earlier resume_id.
        resume_id = resume_store.record_uploaded_resume(
            conn, candidate_id, filename, contents, stored_path,
            status="PARSED" if result.status == "SUCCESS" else "FAILED",
        )

        if result.status != "SUCCESS":
            return JSONResponse(
                status_code=422,
                content={
                    "status": "FAILED",
                    "errors": result.errors,
                    "message": "Resume could not be parsed into a candidate profile.",
                },
            )

        profile, version = profile_store.save_resume_extracted_profile(
            conn, candidate_id, result.profile, resume_id=resume_id
        )

        return {
            "status": "SUCCESS",
            "resume_id": resume_id,
            "profile_version": version,
            "warnings": result.warnings,
            "errors": result.errors,
            "profile": _profile_response(profile),
        }
    finally:
        conn.close()


@app.get("/api/candidates/{candidate_id}/resumes")
def list_candidate_resumes(candidate_id: str):
    """Every resume version on file for this candidate (item 4/9: the
    profile page's resume-management list) -- filename, resume_version
    label, uploaded_at, status, resume_id. Never deletes/overwrites
    historical rows; this is a pure read."""
    conn = db_mod.get_conn()
    try:
        try:
            profile_store.get_candidate(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))
        return {"resumes": resume_store.list_resumes_for_candidate(conn, candidate_id)}
    finally:
        conn.close()


@app.get("/api/candidates/{candidate_id}/profile-versions")
def list_candidate_profile_versions(candidate_id: str):
    """Every profile version for this candidate, with its source
    resume's filename/uploaded_at joined in where known (item 5: the
    "Current profile / Resume v1 / v2 / ..." selector on search
    create/edit)."""
    conn = db_mod.get_conn()
    try:
        try:
            versions = profile_store.list_profile_versions(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))
        return {"profile_versions": versions}
    finally:
        conn.close()


# ------------------------------------------ search provider settings (14.2)

@app.get("/api/settings/search-providers")
def list_search_provider_settings():
    return {"providers": sp_settings_api.list_provider_settings()}


# Phase 14.2 (capability-UI fix): shorter aliases at the exact paths
# requested -- GET /api/search-providers, POST .../test, .../enable,
# .../disable. Same underlying logic as the /api/settings/... routes
# above (sp_settings_api), never a second implementation.
@app.get("/api/search-providers")
def list_search_providers_alias():
    return {"providers": sp_settings_api.list_provider_settings()}


@app.post("/api/search-providers/{provider}/test")
def test_search_provider_alias(provider: str):
    try:
        return sp_settings_api.test_provider(provider)
    except sp_settings_api.SearchProviderSettingsError as error:
        raise HTTPException(400, str(error))


@app.post("/api/search-providers/{provider}/enable")
def enable_search_provider_alias(provider: str):
    try:
        return {"providers": sp_settings_api.set_enabled(provider, True)}
    except sp_settings_api.SearchProviderSettingsError as error:
        raise HTTPException(400, str(error))


@app.post("/api/search-providers/{provider}/disable")
def disable_search_provider_alias(provider: str):
    try:
        return {"providers": sp_settings_api.set_enabled(provider, False)}
    except sp_settings_api.SearchProviderSettingsError as error:
        raise HTTPException(400, str(error))


@app.post("/api/settings/search-providers/order")
def set_search_provider_order(payload: SearchProviderOrderIn):
    try:
        return {"providers": sp_settings_api.set_order(payload.order)}
    except sp_settings_api.SearchProviderSettingsError as error:
        raise HTTPException(400, str(error))


@app.post("/api/settings/search-providers/{provider}/enable")
def enable_search_provider(provider: str):
    try:
        return {"providers": sp_settings_api.set_enabled(provider, True)}
    except sp_settings_api.SearchProviderSettingsError as error:
        raise HTTPException(400, str(error))


@app.post("/api/settings/search-providers/{provider}/disable")
def disable_search_provider(provider: str):
    try:
        return {"providers": sp_settings_api.set_enabled(provider, False)}
    except sp_settings_api.SearchProviderSettingsError as error:
        raise HTTPException(400, str(error))


@app.post("/api/settings/search-providers/{provider}/key")
def set_search_provider_key(provider: str, payload: SearchProviderKeyIn):
    """Stores the key in .env only (never the production DB, never any
    job/candidate record, never echoed back) -- see
    api/search_provider_settings_store.py's set_key()."""
    try:
        return sp_settings_api.set_key(provider, payload.api_key)
    except sp_settings_api.SearchProviderSettingsError as error:
        raise HTTPException(400, str(error))


@app.delete("/api/settings/search-providers/{provider}/key")
def remove_search_provider_key(provider: str):
    try:
        return sp_settings_api.remove_key(provider)
    except sp_settings_api.SearchProviderSettingsError as error:
        raise HTTPException(400, str(error))


@app.post("/api/settings/search-providers/{provider}/test")
def test_search_provider(provider: str):
    """A real, single, minimal live call -- only when a key is
    actually configured, only on explicit user action (the [Test]
    button). Never called automatically."""
    try:
        return sp_settings_api.test_provider(provider)
    except sp_settings_api.SearchProviderSettingsError as error:
        raise HTTPException(400, str(error))


@app.post("/api/settings/search-providers/{provider}/reset-usage")
def reset_search_provider_usage(provider: str, payload: SearchProviderResetUsageIn = SearchProviderResetUsageIn()):
    try:
        return {"providers": sp_settings_api.reset_usage(provider, payload.window)}
    except sp_settings_api.SearchProviderSettingsError as error:
        raise HTTPException(400, str(error))


# ------------------------------------------------------------------ sources

@app.get("/api/sources")
def list_sources():
    summary = search_store.source_capability_summary()
    return {
        "sources": [{"name": s, "status": "ENABLED"} for s in search_store.enabled_sources()],
        "enabled": summary["enabled"],
        "unavailable": summary["unavailable"],
    }


# -------------------------------------------------------------- searches

@app.post("/api/candidates/{candidate_id}/searches")
def create_search(candidate_id: str, payload: SavedSearchCreate):
    conn = db_mod.get_conn()
    try:
        try:
            profile_store.get_candidate(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))
        try:
            saved_search_id = search_store.create_saved_search(conn, candidate_id, payload)
        except search_store.SearchStoreError as error:
            raise HTTPException(400, str(error))
        return search_store.get_saved_search(conn, saved_search_id)
    finally:
        conn.close()


@app.get("/api/candidates/{candidate_id}/searches")
def list_searches(candidate_id: str):
    conn = db_mod.get_conn()
    try:
        return {"searches": search_store.list_saved_searches(conn, candidate_id)}
    finally:
        conn.close()


@app.get("/api/searches/{search_id}")
def get_search(search_id: str, candidate_id: str):
    conn = db_mod.get_conn()
    try:
        try:
            return search_store.get_saved_search_for_candidate(conn, search_id, candidate_id)
        except search_store.SearchStoreError as error:
            raise HTTPException(404, str(error))
    finally:
        conn.close()


@app.put("/api/searches/{search_id}")
@app.patch("/api/searches/{search_id}")
def update_search(search_id: str, candidate_id: str, update: SavedSearchUpdate):
    conn = db_mod.get_conn()
    try:
        try:
            search_store.get_saved_search_for_candidate(conn, search_id, candidate_id)
            return search_store.update_saved_search(conn, search_id, update)
        except search_store.SearchStoreError as error:
            raise HTTPException(404 if "Unknown" in str(error) else 400, str(error))
    finally:
        conn.close()


@app.delete("/api/searches/{search_id}")
def delete_search(search_id: str, candidate_id: str):
    conn = db_mod.get_conn()
    try:
        try:
            search_store.get_saved_search_for_candidate(conn, search_id, candidate_id)
            search_store.archive_saved_search(conn, search_id)
        except search_store.SearchStoreError as error:
            raise HTTPException(404, str(error))
        return {"status": "ARCHIVED"}
    finally:
        conn.close()


@app.post("/api/searches/{search_id}/run")
def run_search(search_id: str, candidate_id: str):
    conn = db_mod.get_conn()
    try:
        try:
            saved_search = search_store.get_saved_search_for_candidate(conn, search_id, candidate_id)
        except search_store.SearchStoreError as error:
            raise HTTPException(404, str(error))

        try:
            trigger_result = search_store.trigger_run(conn, db_mod.DEV_DB, saved_search)
        except search_store.SearchStoreError as error:
            raise HTTPException(409, str(error))

        if trigger_result["already_running"]:
            # Persisted-DB-state guard (search_store.get_active_run_for_
            # search()) -- a second "Run Now" while this search already
            # has a QUEUED/RUNNING run never creates another run or
            # spawns another worker. 409 (not 200) so the UI can
            # distinguish "here is your new run" from "search is
            # already running" without inspecting response fields on
            # every successful call; the existing run's own id/status
            # are still exposed in the body for the caller to act on.
            raise HTTPException(409, {
                "error": "already_running",
                "message": "This search is already running.",
                "run_id": trigger_result["search_run_id"],
                "status": trigger_result["status"],
            })

        return {"run_id": trigger_result["search_run_id"], "status": trigger_result["status"]}
    finally:
        conn.close()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, candidate_id: str):
    conn = db_mod.get_conn()
    try:
        try:
            return search_store.get_run_status_for_candidate(conn, run_id, candidate_id)
        except search_store.SearchStoreError as error:
            raise HTTPException(404, str(error))
    finally:
        conn.close()


@app.get("/api/searches/{search_id}/results")
def get_results(search_id: str, candidate_id: str):
    conn = db_mod.get_conn()
    try:
        try:
            saved_search = search_store.get_saved_search_for_candidate(conn, search_id, candidate_id)
        except search_store.SearchStoreError as error:
            raise HTTPException(404, str(error))
        try:
            results = results_store.get_results_for_saved_search(
                conn, saved_search["candidate_id"], search_id,
                pinned_profile_version=saved_search.get("profile_version"),
            )
        except results_store.ResultsNotReadyError as error:
            raise HTTPException(409, str(error))
        # run_id is resolved via the SAME single function
        # (get_latest_usable_run_id_for_search) that get_source_filter_
        # summary() below uses internally -- one canonical "current run"
        # for this saved search, never two independent computations
        # that could drift and show one run's results/summary next to
        # a DIFFERENT run's source audit. Selects the newest run that
        # is not RUNNING/QUEUED AND has at least one real
        # search_run_sources audit row (falling back to the newest run
        # overall if no run meets both conditions) -- see that
        # function's own docstring, and migrate_v5... / api/db.py's
        # ensure_dev_db() fix for the earlier, related bug this closes.
        run_id = search_store.get_latest_usable_run_id_for_search(conn, search_id)
        sources = search_store.get_source_filter_summary(conn, search_id)
        # Additive only -- existing clients that read response["results"]/
        # ["sources"] are completely unaffected by the new run_id key.
        return {"run_id": run_id, "results": results, "sources": sources}
    finally:
        conn.close()


# ---------------------------------------------------------- manual import

@app.post("/api/candidates/{candidate_id}/jobs/import")
def import_job(candidate_id: str, payload: ManualJobImportIn):
    """
    Phase 13 broad-discovery, Part 13 -- import one job the candidate is
    already viewing in their own browser, for sources with no
    authorized automated acquisition path (see scripts/manual_import.py
    and scripts/source_capabilities.py's manual_import_available flag).
    Reuses the exact same normalize -> eligibility -> score -> persist
    pipeline search_worker.py uses for adapter-discovered jobs (via the
    new search_worker.import_manual_job()) -- no second scoring engine.
    """
    conn = db_mod.get_conn()
    try:
        try:
            profile_store.get_candidate(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))

        try:
            manual_import.validate_manual_import(payload.model_dump())
        except manual_import.ManualImportValidationError as error:
            raise HTTPException(422, str(error))

        raw_job = manual_import.build_manual_import_raw_job(payload.model_dump())

        try:
            result = search_worker.import_manual_job(conn, candidate_id, raw_job)
        except search_worker.ManualImportError as error:
            raise HTTPException(409, str(error))

        return {
            "job_id": result.job_id,
            "source": result.source,
            "company": result.company,
            "title": result.title,
            "eligible": result.eligible,
            "eligibility_reason": result.eligibility_reason,
            "score": result.score,
            "priority": result.priority,
            "status": result.status,
            "matched_skills": result.matched_skills,
            "missing_skills": result.missing_skills,
        }
    finally:
        conn.close()


@app.patch("/api/candidates/{candidate_id}/jobs/{job_id}/status")
def update_job_status(candidate_id: str, job_id: str, update: JobStatusUpdate):
    """
    The one missing write path this project's own audit found: moves
    THIS candidate's own candidate_job_matches.candidate_status
    forward (shortlist / approve / mark-applied / record an interview
    stage / ...), reusing config/application_schema.json's existing
    status lifecycle -- never a second status taxonomy. Never affects
    the GLOBAL jobs.status column (single-candidate-era, not
    per-candidate -- see search_worker.py's own documented limitation)
    or any other candidate's row for the same job.

    Enforces the one non-negotiable safety gate: APPLICATION_STARTED/
    APPLIED is unreachable unless already APPROVED -- this endpoint
    itself never submits anything, it only records a status a human
    explicitly set.
    """
    conn = db_mod.get_conn()
    try:
        try:
            profile_store.get_candidate(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))

        try:
            return application_lifecycle.transition_candidate_job_status(
                conn, candidate_id, job_id, update.status
            )
        except application_lifecycle.ApplicationLifecycleError as error:
            message = str(error)
            status_code = 404 if message.startswith("No match record") else 400
            raise HTTPException(status_code, message)
    finally:
        conn.close()


@app.patch("/api/candidates/{candidate_id}/jobs/{job_id}/follow-up")
def update_job_follow_up_date(candidate_id: str, job_id: str, payload: FollowUpDateIn):
    """
    Phase 6 audit fix: follow_up_date was a schema field
    (config/application_schema.json's legacy job-record shape) with no
    API route anywhere reading or writing it. Correctly scoped to
    candidate_job_matches (candidate+job, migrate_v15_follow_up_date.py)
    -- never the shared, global `jobs` table, which would leak one
    candidate's own follow-up reminder onto every other candidate who
    also matched the same job.
    """
    conn = db_mod.get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM candidate_job_matches WHERE candidate_id = ? AND job_id = ?",
            (candidate_id, job_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"No match record for candidate {candidate_id!r} and job {job_id!r}.")
        conn.execute(
            "UPDATE candidate_job_matches SET follow_up_date = ? WHERE candidate_id = ? AND job_id = ?",
            (payload.follow_up_date, candidate_id, job_id),
        )
        conn.commit()
        return {"candidate_id": candidate_id, "job_id": job_id, "follow_up_date": payload.follow_up_date}
    finally:
        conn.close()


@app.get("/api/candidates/{candidate_id}/jobs/{job_id}/status-history")
def get_job_status_history(candidate_id: str, job_id: str):
    """Ordered transition history for THIS candidate's own status on
    THIS job (see migrate_v6_status_history.py) -- scoped identically
    to update_job_status() above: 404 if the candidate doesn't exist or
    has no match record for this job, never another candidate's
    history for the same global job."""
    conn = db_mod.get_conn()
    try:
        try:
            profile_store.get_candidate(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))

        try:
            current = application_lifecycle.get_candidate_job_status(conn, candidate_id, job_id)
        except application_lifecycle.ApplicationLifecycleError as error:
            raise HTTPException(404, str(error))

        history = application_lifecycle.get_candidate_job_status_history(conn, candidate_id, job_id)
        return {"candidate_id": candidate_id, "job_id": job_id, "current_status": current["candidate_status"], "history": history}
    finally:
        conn.close()


def _generate_and_serve_report(candidate_id, job_id_filter=None, run_ids_filter=None, resume_overrides_by_job=None, filename_suffix="", search_id=None, search_run_id=None):
    import generate_run_report

    out_dir = ROOT / "data" / "applications" / "reports_dev"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{candidate_id}{filename_suffix}_report.xlsx"

    result = generate_run_report.generate(
        str(db_mod.DEV_DB), candidate_id, str(out_path),
        job_id_filter=job_id_filter, run_ids_filter=run_ids_filter,
        resume_overrides_by_job=resume_overrides_by_job,
        search_id=search_id, search_run_id=search_run_id,
    )

    return FileResponse(
        result["workbook_path"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"jobos_report_{candidate_id}{filename_suffix}.xlsx",
    )


@app.get("/api/candidates/{candidate_id}/report")
def download_report(candidate_id: str):
    conn = db_mod.get_conn()
    try:
        try:
            profile_store.get_candidate(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))
    finally:
        conn.close()

    return _generate_and_serve_report(candidate_id)


@app.get("/api/searches/{search_id}/report")
def download_search_report(search_id: str, candidate_id: str):
    """
    Root-cause fix for a real bug (item 10/11): this used to return the
    SAME candidate-WIDE report download_report() below does -- every
    job ever discovered across ALL of this candidate's searches/runs --
    even though the GUI's results page for THIS search shows only its
    own scoped result count. Clicking "Download Excel" from a search
    showing e.g. 75 results could produce a workbook with 1500+ rows.

    Now scoped via the SAME canonical function results_store.
    _job_ids_for_search() / list_run_ids_for_search() already use for
    the JSON results API -- one canonical run-scoped query, reused
    here, never a second/drifting implementation -- so the export can
    never show a different job count than the UI for the same search.
    """
    conn = db_mod.get_conn()
    try:
        try:
            search_store.get_saved_search_for_candidate(conn, search_id, candidate_id)
        except search_store.SearchStoreError as error:
            raise HTTPException(404, str(error))
        scoped_job_ids = results_store._job_ids_for_search(conn, candidate_id, search_id)
        scoped_run_ids = search_store.list_run_ids_for_search(conn, search_id)
        scoped_latest_run_id = search_store.get_latest_usable_run_id_for_search(conn, search_id)
        resume_overrides = results_store.get_scoped_resume_overrides(conn, candidate_id, search_id)
    finally:
        conn.close()

    return _generate_and_serve_report(
        candidate_id, job_id_filter=scoped_job_ids, run_ids_filter=scoped_run_ids,
        resume_overrides_by_job=resume_overrides, filename_suffix=f"_{search_id}",
        search_id=search_id, search_run_id=scoped_latest_run_id,
    )


@app.get("/api/candidates/{candidate_id}/dashboard")
def dashboard(candidate_id: str):
    conn = db_mod.get_conn()
    try:
        try:
            profile_store.get_candidate(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))

        searches = search_store.list_saved_searches(conn, candidate_id)
        for s in searches:
            run_ids = search_store.list_run_ids_for_search(conn, s["saved_search_id"])
            s["run_count"] = len(run_ids)
            s["latest_run"] = None
            if run_ids:
                latest = conn.execute(
                    "SELECT search_run_id, status, jobs_ready, jobs_eligible, created_at "
                    "FROM search_runs WHERE search_run_id IN ({}) ORDER BY created_at DESC LIMIT 1".format(
                        ",".join("?" for _ in run_ids)
                    ),
                    run_ids,
                ).fetchone()
                if latest:
                    s["latest_run"] = dict(latest)
                    # Additive: per-source execution audit (Phase 1) for
                    # this search's most recent run -- which sources were
                    # attempted, which returned zero, which failed, not
                    # just the run-wide aggregate counts above.
                    s["latest_run"]["sources"] = search_store.get_run_sources(conn, latest["search_run_id"])

        try:
            profile = profile_store.load_active_profile(conn, candidate_id)
            profile_status = profile.metadata.profile_status.value
        except profile_store.ProfileStoreError:
            profile_status = None

        try:
            summary = results_store.get_dashboard_summary(conn, candidate_id)
        except Exception:
            summary = None

        source_caps = search_store.source_capability_summary()

        # Additive, isolated COUNT queries only -- deliberately NOT
        # touching results_store.get_dashboard_summary() (the
        # non-negotiable, already-validated 185/46-job scoring/
        # eligibility aggregate) to surface these two new-feature
        # counts on the dashboard.
        tailored_resume_count = conn.execute(
            "SELECT COUNT(DISTINCT job_id) FROM tailored_resumes WHERE candidate_id = ? AND status = 'READY'",
            (candidate_id,),
        ).fetchone()[0]
        interview_prep_count = conn.execute(
            "SELECT COUNT(*) FROM interview_preparations WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()[0]

        return {
            "candidate_id": candidate_id,
            "profile_status": profile_status,
            "searches": searches,
            "summary": summary,
            "sources": source_caps,
            "tailored_resume_count": tailored_resume_count,
            "interview_prep_count": interview_prep_count,
        }
    finally:
        conn.close()


@app.get("/api/candidates/{candidate_id}/follow-ups")
def list_follow_ups(candidate_id: str):
    """
    Candidate-scoped list of every job this candidate has set a
    follow_up_date on (candidate_job_matches, migrate_v15_follow_up_
    date.py -- never the global jobs table), soonest first. Serves
    two callers: the dashboard's "Follow-ups" widget, and the n8n
    Follow-Up Reminder workflow (see n8n/workflows/) -- one query, no
    duplicated logic. A candidate must supply their OWN candidate_id
    (same DB-query-scoping convention as every other route in this
    API, which has no separate auth layer -- see web/app.js's own
    docstring on this).
    """
    conn = db_mod.get_conn()
    try:
        try:
            profile_store.get_candidate(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))
        rows = conn.execute(
            """
            SELECT cjm.job_id, cjm.follow_up_date, cjm.candidate_status,
                   j.title, j.company, j.job_url, j.application_url
            FROM candidate_job_matches cjm
            JOIN jobs j ON j.job_id = cjm.job_id
            WHERE cjm.candidate_id = ? AND cjm.follow_up_date IS NOT NULL
            ORDER BY cjm.follow_up_date ASC
            """,
            (candidate_id,),
        ).fetchall()
        return {"follow_ups": [dict(r) for r in rows]}
    finally:
        conn.close()


# ---------------------------------------------------------- resume tailoring

@app.post("/api/candidates/{candidate_id}/jobs/{job_id}/tailor-resume")
def tailor_resume(candidate_id: str, job_id: str, payload: TailorResumeIn):
    conn = db_mod.get_conn()
    try:
        try:
            profile = profile_store.load_active_profile(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))
        if profile.metadata.profile_status.value != "CONFIRMED":
            raise HTTPException(409, "Profile must be CONFIRMED before tailoring a resume.")
        try:
            result = resume_tailoring.create_tailored_resume(
                conn, candidate_id, payload.base_resume_id, job_id, profile
            )
        except resume_tailoring.ResumeTailoringError as error:
            raise HTTPException(404, str(error))
        return result
    finally:
        conn.close()


@app.get("/api/candidates/{candidate_id}/jobs/{job_id}/tailored-resumes")
def list_tailored_resumes_for_job(candidate_id: str, job_id: str):
    conn = db_mod.get_conn()
    try:
        return {"tailored_resumes": resume_tailoring.list_tailored_resumes(conn, candidate_id, job_id=job_id)}
    finally:
        conn.close()


@app.get("/api/candidates/{candidate_id}/tailored-resumes/{tailored_resume_id}")
def get_tailored_resume(candidate_id: str, tailored_resume_id: str):
    conn = db_mod.get_conn()
    try:
        try:
            return resume_tailoring.get_tailored_resume(conn, candidate_id, tailored_resume_id)
        except resume_tailoring.ResumeTailoringError as error:
            raise HTTPException(404, str(error))
    finally:
        conn.close()


@app.get("/api/candidates/{candidate_id}/tailored-resumes/{tailored_resume_id}/download")
def download_tailored_resume(candidate_id: str, tailored_resume_id: str):
    conn = db_mod.get_conn()
    try:
        try:
            record = resume_tailoring.get_tailored_resume(conn, candidate_id, tailored_resume_id)
        except resume_tailoring.ResumeTailoringError as error:
            raise HTTPException(404, str(error))
    finally:
        conn.close()
    text = resume_tailoring.render_plain_text(record)
    return _plain_text_response(text, f"{tailored_resume_id}.txt")


def _plain_text_response(text, filename):
    from fastapi import Response
    return Response(content=text, media_type="text/plain", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# --------------------------------------------------------- company research

@app.post("/api/candidates/{candidate_id}/company-research")
def run_company_research(candidate_id: str, payload: CompanyResearchIn):
    conn = db_mod.get_conn()
    try:
        try:
            profile_store.get_candidate(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))
        if payload.job_id is not None:
            # job_id is optional context (jobs is a global table, so no
            # per-candidate ownership concept applies -- see
            # resume_tailoring.py's own comment on this same point),
            # but a caller-supplied id that doesn't exist at all should
            # never be silently persisted as if it were valid.
            job_row = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (payload.job_id,)).fetchone()
            if job_row is None:
                raise HTTPException(404, f"Unknown job: {payload.job_id!r}")
        return company_research.research_company(conn, candidate_id, payload.company_name, job_id=payload.job_id)
    finally:
        conn.close()


@app.get("/api/candidates/{candidate_id}/company-research")
def list_company_research_route(candidate_id: str, company_name: str | None = None):
    conn = db_mod.get_conn()
    try:
        return {"company_research": company_research.list_company_research(conn, candidate_id, company_name=company_name)}
    finally:
        conn.close()


@app.get("/api/candidates/{candidate_id}/company-research/{company_research_id}")
def get_company_research_route(candidate_id: str, company_research_id: str):
    conn = db_mod.get_conn()
    try:
        try:
            return company_research.get_company_research(conn, candidate_id, company_research_id)
        except ValueError as error:
            raise HTTPException(404, str(error))
    finally:
        conn.close()


# ------------------------------------------------------- interview prep

@app.post("/api/candidates/{candidate_id}/interview-prep")
def create_interview_prep_route(candidate_id: str, payload: InterviewPrepIn):
    conn = db_mod.get_conn()
    try:
        try:
            profile = profile_store.load_active_profile(conn, candidate_id)
        except profile_store.ProfileStoreError as error:
            raise HTTPException(404, str(error))
        if profile.metadata.profile_status.value != "CONFIRMED":
            raise HTTPException(409, "Profile must be CONFIRMED before generating interview preparation.")
        try:
            return interview_prep.create_interview_prep(
                conn, candidate_id, payload.job_id, profile,
                resume_id=payload.resume_id, company_research_id=payload.company_research_id,
            )
        except ValueError as error:
            raise HTTPException(404, str(error))
    finally:
        conn.close()


@app.get("/api/candidates/{candidate_id}/jobs/{job_id}/interview-prep")
def get_interview_prep_for_job(candidate_id: str, job_id: str):
    conn = db_mod.get_conn()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT interview_prep_id FROM interview_preparations WHERE candidate_id = ? AND job_id = ?",
            (candidate_id, job_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"No interview preparation exists yet for job {job_id!r}.")
        return interview_prep.get_interview_prep(conn, candidate_id, row["interview_prep_id"])
    finally:
        conn.close()


@app.get("/api/candidates/{candidate_id}/interview-prep/{interview_prep_id}")
def get_interview_prep_route(candidate_id: str, interview_prep_id: str):
    conn = db_mod.get_conn()
    try:
        try:
            return interview_prep.get_interview_prep(conn, candidate_id, interview_prep_id)
        except ValueError as error:
            raise HTTPException(404, str(error))
    finally:
        conn.close()


@app.patch("/api/candidates/{candidate_id}/interview-prep-questions/{question_id}")
def update_interview_answer(candidate_id: str, question_id: str, payload: InterviewAnswerIn):
    conn = db_mod.get_conn()
    try:
        try:
            interview_prep.update_question_answer(
                conn, candidate_id, question_id,
                candidate_answer=payload.candidate_answer, confidence=payload.confidence, notes=payload.notes,
            )
        except ValueError as error:
            raise HTTPException(404, str(error))
        return {"status": "OK"}
    finally:
        conn.close()


@app.patch("/api/candidates/{candidate_id}/interview-prep/{interview_prep_id}/outcome")
def update_interview_outcome(candidate_id: str, interview_prep_id: str, payload: InterviewOutcomeIn):
    conn = db_mod.get_conn()
    try:
        try:
            interview_prep.update_outcome(
                conn, candidate_id, interview_prep_id,
                outcome_status=payload.outcome_status, outcome_notes=payload.outcome_notes,
            )
        except ValueError as error:
            raise HTTPException(404, str(error))
        return {"status": "OK"}
    finally:
        conn.close()


# --------------------------------------------------------------- scheduling

@app.put("/api/searches/{search_id}/schedule")
def set_search_schedule(search_id: str, candidate_id: str, payload: ScheduleSetIn):
    conn = db_mod.get_conn()
    try:
        try:
            search_store.get_saved_search_for_candidate(conn, search_id, candidate_id)
        except search_store.SearchStoreError as error:
            raise HTTPException(404, str(error))
        try:
            return scheduler.set_schedule(
                conn, candidate_id, search_id,
                enabled=payload.enabled, frequency=payload.frequency, timezone_name=payload.timezone,
            )
        except scheduler.SchedulerError as error:
            raise HTTPException(422, str(error))
    finally:
        conn.close()


@app.get("/api/searches/{search_id}/schedule")
def get_search_schedule(search_id: str, candidate_id: str):
    conn = db_mod.get_conn()
    try:
        try:
            search_store.get_saved_search_for_candidate(conn, search_id, candidate_id)
        except search_store.SearchStoreError as error:
            raise HTTPException(404, str(error))
        row = scheduler.get_schedule(conn, search_id)
        return row or {"saved_search_id": search_id, "enabled": False, "frequency": None, "timezone": "UTC",
                       "next_run_at": None, "last_run_at": None, "last_run_status": None, "last_run_error": None}
    finally:
        conn.close()


@app.post("/api/scheduler/run-due")
def run_due_schedules_route():
    """
    Maintenance/automation entry point: processes every currently due,
    enabled schedule ONCE (scheduler.run_due_schedules() -- exactly the
    same bounded primitive scripts/run_scheduled_searches.py's CLI
    wrapper calls), across every candidate. Intended to be invoked by
    an external clock (n8n's own Schedule Trigger node, cron, or
    launchd) -- this route itself is not a scheduler, it does not loop
    or persist any in-memory timer.
    """
    conn = db_mod.get_conn()
    try:
        outcomes = scheduler.run_due_schedules(conn, db_mod.DEV_DB)
        return {"processed": len(outcomes), "outcomes": outcomes}
    finally:
        conn.close()


# -------------------------------------------------------------- static site

@app.get("/")
def serve_index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/profile")
def serve_profile_page():
    return FileResponse(WEB_DIR / "profile.html")


@app.get("/searches")
def serve_searches_list_page():
    return FileResponse(WEB_DIR / "searches.html")


@app.get("/searches/new")
def serve_new_search_page():
    return FileResponse(WEB_DIR / "search_new.html")


@app.get("/import")
def serve_import_page():
    return FileResponse(WEB_DIR / "import_job.html")


@app.get("/settings/search-providers")
def serve_search_provider_settings_page():
    return FileResponse(WEB_DIR / "settings_search_providers.html")


@app.get("/searches/{search_id}/results")
def serve_results_page(search_id: str):
    return FileResponse(WEB_DIR / "results.html")


@app.get("/searches/{search_id}/edit")
def serve_edit_search_page(search_id: str):
    # Reuses search_new.html's own form/resume-selector markup and JS
    # (item 4 of the master task: edit an existing search) -- the page
    # itself detects edit mode from its own URL path at load time
    # (see search_new.html's load()) and switches between POST
    # /api/candidates/{id}/searches (create) and PUT /api/searches/{id}
    # (edit), never a second form/page.
    return FileResponse(WEB_DIR / "search_new.html")


@app.get("/searches/{search_id}")
def serve_search_detail_page(search_id: str):
    return FileResponse(WEB_DIR / "search_detail.html")


@app.get("/dashboard")
def serve_dashboard_page():
    return FileResponse(WEB_DIR / "dashboard.html")


@app.get("/jobs/{job_id}")
def serve_job_workspace_page(job_id: str):
    # Phase 5 UI: resume tailoring / company research / interview prep /
    # follow-up date, all for one job. Loads its own data client-side
    # from ?search_id=... (see job_workspace.html's own load()) --
    # this route itself just serves the static shell, matching every
    # other detail page in this app (search_detail.html, etc).
    return FileResponse(WEB_DIR / "job_workspace.html")


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
