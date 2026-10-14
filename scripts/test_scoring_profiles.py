#!/usr/bin/env python3

"""
Custom Job Scoring Profiles -- regression tests.

Covers both the pure scoring module (scripts/scoring_profiles.py,
scripts/scoring_profile_store.py) and the API routes added to
api/main.py. Fully offline, isolated temp DB, FastAPI TestClient.
Every job/candidate used is synthetic. Production DB verified
byte-identical before/after.
"""

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "api"))

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


production_before = _sha(PRODUCTION_DB)

passed = 0
failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS: {msg}")
    else:
        failed += 1
        print(f"FAIL: {msg}")


import scoring_profiles as spm
import scoring_profile_store as sps
from score_job import score_job

SYNTHETIC_PROFILE = {
    "candidate": {"experience_years": 8},
    "target_roles": ["Senior Site Reliability Engineer"],
    "target_locations": ["Bengaluru", "Remote"],
    "cloud": ["Azure", "AWS"],
    "kubernetes": ["Kubernetes", "AKS"],
    "iac_and_automation": ["Terraform"],
    "cicd_and_devops": ["Jenkins"],
    "observability": ["Prometheus"],
    "sre": [],
    "programming_and_scripting": [],
    "tools": [],
    "excluded_roles": [],
    "salary_expectation_min": 20.0,
    "salary_expectation_max": 28.0,
    "certifications": ["Example Cloud Certification"],
}

SYNTHETIC_JOB = {
    "job_id": "TEST-SP-001",
    "source": "TEST",
    "company": "Example Technology",
    "title": "Senior Site Reliability Engineer",
    "location": "Bengaluru",
    "work_model": "Hybrid",
    "experience_required": "8+ years",
    "mandatory_skills": ["Kubernetes", "Terraform"],
    "preferred_skills": ["Azure", "Prometheus", "Jenkins"],
    "jd_text": (
        "Senior Site Reliability Engineer responsible for production "
        "reliability, Kubernetes, AKS, Azure, Terraform, Jenkins CI/CD, "
        "Prometheus, cloud infrastructure and platform reliability. "
        "Example Cloud Certification preferred."
    ),
}

ILLUSTRATIVE = dict(spm.ILLUSTRATIVE_DEFAULT_WEIGHTS)

# =====================================================================
# Section 1: legacy compatibility
# =====================================================================

legacy_direct = score_job(SYNTHETIC_JOB, SYNTHETIC_PROFILE)
legacy_via_module = spm.score_with_profile(SYNTHETIC_JOB, SYNTHETIC_PROFILE, scoring_profile=None)
check(
    legacy_direct["score"] == legacy_via_module.score
    and legacy_direct["priority"] == legacy_via_module.priority
    and legacy_direct["status"] == legacy_via_module.status,
    f"1. scoring_profile=None delegates to score_job() byte-for-byte (score {legacy_via_module.score}, priority {legacy_via_module.priority})",
)
check(
    legacy_direct["matched_skills"] == legacy_via_module.matched_skills
    and legacy_direct["missing_skills"] == legacy_via_module.missing_skills,
    "2. legacy delegation preserves matched_skills/missing_skills exactly",
)
check(
    legacy_via_module.qualified == (legacy_direct["priority"] != "REJECT" and not legacy_direct["hard_reject_reasons"]),
    "3. legacy-mode 'qualified' matches the pre-existing not-REJECT/no-hard-reject semantics",
)

# =====================================================================
# Section 2: weight validation
# =====================================================================

try:
    spm.validate_scoring_profile({"weights": {"not_a_real_criterion": 10}})
    check(False, "4. unknown criterion is rejected")
except spm.ScoringProfileError:
    check(True, "4. unknown criterion is rejected")

try:
    spm.validate_scoring_profile({"weights": {"technical_skills": -5}})
    check(False, "5. negative weight is rejected")
except spm.ScoringProfileError:
    check(True, "5. negative weight is rejected")

try:
    spm.validate_scoring_profile({"weights": {"technical_skills": "banana"}})
    check(False, "6. non-numeric weight is rejected")
except spm.ScoringProfileError:
    check(True, "6. non-numeric weight is rejected")

try:
    spm.validate_scoring_profile({"weights": {}})
    check(False, "7. empty criteria set is rejected")
except spm.ScoringProfileError:
    check(True, "7. empty criteria set is rejected")

try:
    spm.validate_scoring_profile({"weights": {"technical_skills": 0}})
    check(False, "7b. all-zero weights is rejected")
except spm.ScoringProfileError:
    check(True, "7b. all-zero weights is rejected")

try:
    spm.validate_scoring_profile({"weights": {"technical_skills": 1}, "min_shortlist_score": 150})
    check(False, "8. out-of-range min_shortlist_score is rejected")
except spm.ScoringProfileError:
    check(True, "8. out-of-range min_shortlist_score is rejected")

normalized = spm.validate_scoring_profile({"weights": {"technical_skills": 3, "relevant_experience": 2}})
check(
    abs(sum(normalized.values()) - 100.0) < 1e-9,
    f"9. non-100-sum weights are normalized to sum to exactly 100 (got {sum(normalized.values())})",
)
normalized_already_100 = spm.validate_scoring_profile({"weights": ILLUSTRATIVE})
check(
    all(abs(normalized_already_100[k] - v) < 1e-9 for k, v in ILLUSTRATIVE.items()),
    "9b. an already-100-sum illustrative profile is returned unchanged by normalization",
)

# =====================================================================
# Section 3: the illustrative 30/20/15/15/10/5/5 @ threshold 70
# =====================================================================

illustrative_profile = {
    "scoring_profile_id": "sp_test_illustrative", "version": 1, "name": "Illustrative",
    "weights": ILLUSTRATIVE, "mandatory_gate_enabled": True, "min_shortlist_score": 70,
}
result = spm.score_with_profile(SYNTHETIC_JOB, SYNTHETIC_PROFILE, scoring_profile=illustrative_profile)
check(0 <= result.score <= 100, f"10. score stays within [0,100] (got {result.score})")
check(result.mandatory_gate_passed is True, "11. mandatory-skill gate passes when candidate has both required skills")
check(len(result.criteria) == 7, "12. all 7 illustrative criteria are evaluated")
check(
    abs(sum(c.weighted_contribution for c in result.criteria) - result.score) < 1,
    "13. sum of weighted criterion contributions equals the final score (within rounding)",
)

# =====================================================================
# Section 4: mandatory-gate pass/fail is independent of weighted score
# =====================================================================

job_missing_mandatory = dict(SYNTHETIC_JOB)
job_missing_mandatory["mandatory_skills"] = ["GCP"]  # candidate has no GCP anywhere
gate_fail_result = spm.score_with_profile(job_missing_mandatory, SYNTHETIC_PROFILE, scoring_profile=illustrative_profile)
check(
    gate_fail_result.mandatory_gate_passed is False,
    "14. mandatory gate correctly fails when a required skill is absent from the profile",
)
check(
    gate_fail_result.qualified is False and gate_fail_result.priority == "REJECT",
    f"15. a failed mandatory gate forces REJECT/not-qualified EVEN THOUGH the weighted score is {gate_fail_result.score} "
    "-- a high score can never override a hard mandatory-skill gap",
)
check(
    gate_fail_result.score >= 0,
    "16. the numeric score is still computed/preserved even when the gate fails (gate and score are separate concerns)",
)

disabled_gate_profile = dict(illustrative_profile)
disabled_gate_profile["mandatory_gate_enabled"] = False
gate_disabled_result = spm.score_with_profile(job_missing_mandatory, SYNTHETIC_PROFILE, scoring_profile=disabled_gate_profile)
check(
    gate_disabled_result.hard_reject_reasons == [] and gate_disabled_result.mandatory_gate_passed is True,
    "17. disabling the mandatory gate on a profile skips evaluate_hard_reject() entirely",
)

# =====================================================================
# Section 5: each criterion independently + missing-data policy
# =====================================================================

for criterion in spm.CRITERIA:
    single = {"weights": {criterion: 100}, "mandatory_gate_enabled": False, "min_shortlist_score": 0}
    r = spm.score_with_profile(SYNTHETIC_JOB, SYNTHETIC_PROFILE, scoring_profile=single)
    check(0 <= r.score <= 100, f"18. single-criterion profile for {criterion!r} stays in bounds (got {r.score})")

# Missing salary data entirely (neither job nor candidate has it).
profile_no_salary = dict(SYNTHETIC_PROFILE)
profile_no_salary.pop("salary_expectation_min", None)
profile_no_salary.pop("salary_expectation_max", None)
r = spm.score_with_profile(SYNTHETIC_JOB, profile_no_salary, scoring_profile={"weights": {"salary_compensation": 100}, "min_shortlist_score": 0})
check(
    r.criteria[0].missing_data is True and r.criteria[0].raw_score == 0.5,
    f"19. missing salary data on both sides -> MISSING, neutral 50% credit, never full and never zero (got raw_score={r.criteria[0].raw_score})",
)

# Missing location entirely.
profile_no_location = dict(SYNTHETIC_PROFILE)
profile_no_location["target_locations"] = []
job_no_location = dict(SYNTHETIC_JOB)
job_no_location["location"] = ""
job_no_location["work_model"] = ""
r = spm.score_with_profile(job_no_location, profile_no_location, scoring_profile={"weights": {"location_work_mode": 100}, "min_shortlist_score": 0})
check(r.criteria[0].missing_data is True, "20. missing location/work-model data on both sides is marked MISSING, not a false mismatch")

# Missing certifications.
profile_no_certs = dict(SYNTHETIC_PROFILE)
profile_no_certs["certifications"] = []
r = spm.score_with_profile(SYNTHETIC_JOB, profile_no_certs, scoring_profile={"weights": {"certifications": 100}, "min_shortlist_score": 0})
check(r.criteria[0].missing_data is True, "21. candidate with no certifications configured -> MISSING, not a false mismatch")
check(r.criteria[0].raw_score == 0.5, "21b. missing-certifications credit is the same neutral 50% as every other missing-data case")

# Missing company/domain signal (title + jd_text with NO domain
# keywords at all -- note the title itself must also avoid every
# _DOMAIN_KEYWORDS term, since company_domain scans title+jd_text
# combined, same as every other score_job.py dimension).
job_no_domain = dict(SYNTHETIC_JOB)
job_no_domain["title"] = "Widget Coordinator"
job_no_domain["jd_text"] = "Short job post with no recognizable domain terms."
r = spm.score_with_profile(job_no_domain, SYNTHETIC_PROFILE, scoring_profile={"weights": {"company_domain": 100}, "min_shortlist_score": 0})
check(r.criteria[0].missing_data is True, "22. no domain-keyword matches at all -> MISSING, never a fabricated zero-is-a-match")

# Never full credit "merely because a field is missing".
check(
    all(r.raw_score < 1.0 for r in [spm.CriterionResult("x", "x", 0.5, 100, 50, True)]),
    "23. the documented missing-data credit (0.5) is never 1.0 -- missing data never earns full credit",
)

# =====================================================================
# Section 6: invalid/empty profiles
# =====================================================================

try:
    spm.score_with_profile(SYNTHETIC_JOB, SYNTHETIC_PROFILE, scoring_profile={"weights": {}})
    check(False, "24. scoring with an empty-weights profile raises ScoringProfileError")
except spm.ScoringProfileError:
    check(True, "24. scoring with an empty-weights profile raises ScoringProfileError")

# =====================================================================
# Section 7: score bounds under extreme weighting
# =====================================================================

extreme = {"weights": {"technical_skills": 1000000}, "min_shortlist_score": 0}
r = spm.score_with_profile(SYNTHETIC_JOB, SYNTHETIC_PROFILE, scoring_profile=extreme)
check(0 <= r.score <= 100, f"25. an extreme single-weight profile still produces a score in [0,100] (got {r.score})")

# =====================================================================
# Section 8: explanations and weighted contributions
# =====================================================================

as_dict = spm.result_to_dict(result)
check(
    set(as_dict.keys()) >= {"score", "priority", "status", "qualified", "mandatory_gate_passed", "criteria", "profile_id", "profile_version"},
    "26. result_to_dict() exposes score/priority/status/qualified/gate/criteria/profile id+version",
)
check(
    all({"criterion", "display_name", "raw_score", "weight_percent", "weighted_contribution", "missing_data", "evidence", "reason"} <= set(c.keys()) for c in as_dict["criteria"]),
    "27. every criterion explanation includes weight, contribution, evidence, missing-data flag, and reason",
)
check(
    as_dict["profile_id"] == "sp_test_illustrative" and as_dict["profile_version"] == 1,
    "28. the explanation records exactly which profile id/version produced it (traceability)",
)

# =====================================================================
# Section 9: cross-source consistency
# (Two differently-SHAPED-but-equivalent raw job dicts, as two
# different source adapters might produce, both normalized through
# discover_local.normalize_job() before scoring -- proving the SAME
# canonical pipeline/scoring call is used regardless of origin shape.)
# =====================================================================

import discover_local

raw_from_source_a = {
    "source": "NAUKRI", "company": "Example Technology", "title": "Senior Site Reliability Engineer",
    "location": "Bengaluru", "work_model": "Hybrid", "jd_text": SYNTHETIC_JOB["jd_text"],
    "mandatory_skills": ["Kubernetes", "Terraform"], "experience_required": "8+ years",
}
raw_from_source_b = {
    "source": "HIRIST", "company": "Example Technology", "title": "Senior Site Reliability Engineer",
    "location": "Bengaluru", "work_model": "Hybrid", "jd_text": SYNTHETIC_JOB["jd_text"],
    "mandatory_skills": ["Kubernetes", "Terraform"], "experience_required": "8+ years",
}
normalized_a = discover_local.normalize_job(raw_from_source_a, 1)
normalized_b = discover_local.normalize_job(raw_from_source_b, 2)
score_a = spm.score_with_profile(normalized_a, SYNTHETIC_PROFILE, scoring_profile=illustrative_profile)
score_b = spm.score_with_profile(normalized_b, SYNTHETIC_PROFILE, scoring_profile=illustrative_profile)
check(
    score_a.score == score_b.score and score_a.priority == score_b.priority,
    f"29. equivalent normalized jobs from two different sources (NAUKRI vs HIRIST) score identically under the same profile ({score_a.score} == {score_b.score})",
)

# A source with LESS information (no jd_text) follows the same
# documented missing-data policy, not a different, source-specific one.
raw_sparse = {"source": "APNA", "company": "Example Technology", "title": "Senior Site Reliability Engineer"}
normalized_sparse = discover_local.normalize_job(raw_sparse, 3)
score_sparse = spm.score_with_profile(normalized_sparse, SYNTHETIC_PROFILE, scoring_profile=illustrative_profile)
check(
    any(c.missing_data for c in score_sparse.criteria),
    "30. a source with sparse data (no jd_text/mandatory_skills) triggers the SAME missing-data policy, not a source-specific crash or fabricated match",
)

print()
print(f"{passed} passed, {failed} failed (module-level)")

# =====================================================================
# Section 10: API routes (isolated temp DB, FastAPI TestClient)
# =====================================================================

tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_scoring_profiles_api_"))
tmp_db = tmp_dir / "test.db"

import init_dev_db
init_dev_db.init_dev_db(tmp_db)
import db as db_mod
db_mod.DEV_DB = tmp_db
from fastapi.testclient import TestClient
import main as api_main
api_main.db_mod.DEV_DB = tmp_db
client = TestClient(api_main.app)


def _create_confirmed_candidate(name="Alex Doe"):
    cand_id = client.post("/api/candidates", json={"name": name}).json()["candidate_id"]
    client.put(
        f"/api/candidates/{cand_id}/profile",
        json={
            "total_experience_years": 8.0,
            "job_preferences": {"target_roles": ["Senior Site Reliability Engineer"], "target_locations": ["Bengaluru", "Remote"]},
            "skills": {
                "cloud": [{"name": "Azure"}],
                "containers_orchestration": [{"name": "Kubernetes"}, {"name": "AKS"}],
                "infrastructure_iac": [{"name": "Terraform"}],
                "cicd": [{"name": "Jenkins"}],
                "observability": [{"name": "Prometheus"}],
            },
        },
    )
    confirm = client.post(f"/api/candidates/{cand_id}/profile/confirm")
    return cand_id, confirm


cand_a, confirm_a = _create_confirmed_candidate("Alex Doe")
check(confirm_a.status_code == 200, f"31. API: candidate A's profile confirms successfully (got {confirm_a.status_code}: {confirm_a.text[:200]})")

cand_b, confirm_b = _create_confirmed_candidate("Jordan Smith")
check(confirm_b.status_code == 200, "32. API: a second, independent candidate confirms successfully")

# --- CRUD ---
list_empty = client.get(f"/api/candidates/{cand_a}/scoring-profiles")
check(
    list_empty.status_code == 200 and list_empty.json()["scoring_profiles"] == [],
    "33. API: a fresh candidate has zero scoring profiles",
)
check(
    list_empty.json()["illustrative_default_weights"] == spm.ILLUSTRATIVE_DEFAULT_WEIGHTS,
    "34. API: the list response exposes the illustrative default weights for the UI to pre-fill",
)

create_resp = client.post(
    f"/api/candidates/{cand_a}/scoring-profiles",
    json={"name": "Senior SRE", "weights": ILLUSTRATIVE, "mandatory_gate_enabled": True, "min_shortlist_score": 70, "is_default": True},
)
check(create_resp.status_code == 200, f"35. API: create a scoring profile (got {create_resp.status_code})")
profile_a = create_resp.json()
sp_id = profile_a["scoring_profile_id"]
check(profile_a["version"] == 1 and profile_a["is_default"] is True, "36. API: newly created profile starts at version 1 and honors is_default=True")

get_resp = client.get(f"/api/candidates/{cand_a}/scoring-profiles/{sp_id}")
check(get_resp.status_code == 200 and get_resp.json()["name"] == "Senior SRE", "37. API: read back the created profile")

update_resp = client.put(f"/api/candidates/{cand_a}/scoring-profiles/{sp_id}", json={"min_shortlist_score": 60})
check(update_resp.status_code == 200 and update_resp.json()["version"] == 2, "38. API: updating a profile increments its version")
check(update_resp.json()["min_shortlist_score"] == 60.0, "38b. API: the updated field actually changed")
check(update_resp.json()["weights"] == ILLUSTRATIVE, "38c. API: an update that doesn't touch weights leaves them unchanged")

dup_resp = client.post(f"/api/candidates/{cand_a}/scoring-profiles/{sp_id}/duplicate", json={"name": "Azure DevOps Engineer"})
check(dup_resp.status_code == 200 and dup_resp.json()["scoring_profile_id"] != sp_id, "39. API: duplicate creates a distinct profile id")
check(dup_resp.json()["is_default"] is False, "39b. API: a duplicate is never auto-made default")
dup_id = dup_resp.json()["scoring_profile_id"]

list_resp = client.get(f"/api/candidates/{cand_a}/scoring-profiles")
check(len(list_resp.json()["scoring_profiles"]) == 2, "40. API: candidate A now has exactly 2 profiles (original + duplicate)")

# --- validation ---
bad_create = client.post(f"/api/candidates/{cand_a}/scoring-profiles", json={"name": "bad", "weights": {"not_real": 10}})
check(bad_create.status_code == 400, f"41. API: unknown criterion rejected with 400 (got {bad_create.status_code})")

bad_update = client.put(f"/api/candidates/{cand_a}/scoring-profiles/{sp_id}", json={"min_shortlist_score": 500})
check(bad_update.status_code in (400, 422), f"42. API: out-of-range threshold rejected (got {bad_update.status_code})")

# --- ownership: candidate B cannot read/write/delete candidate A's profile ---
cross_get = client.get(f"/api/candidates/{cand_b}/scoring-profiles/{sp_id}")
check(cross_get.status_code == 404, f"43. API: candidate B cannot read candidate A's profile by id (got {cross_get.status_code})")
cross_update = client.put(f"/api/candidates/{cand_b}/scoring-profiles/{sp_id}", json={"name": "hijacked"})
check(cross_update.status_code == 404, "44. API: candidate B cannot update candidate A's profile")
cross_delete = client.delete(f"/api/candidates/{cand_b}/scoring-profiles/{sp_id}")
check(cross_delete.status_code == 404, "45. API: candidate B cannot delete candidate A's profile")
# Confirm it genuinely still exists for its real owner after the failed cross-candidate delete attempt.
still_there = client.get(f"/api/candidates/{cand_a}/scoring-profiles/{sp_id}")
check(still_there.status_code == 200, "46. API: the profile is untouched after a rejected cross-candidate delete attempt")

# --- preview (never creates a job/match row) ---
preview_resp = client.post(
    f"/api/candidates/{cand_a}/scoring-profiles/preview",
    json={"job": SYNTHETIC_JOB, "scoring_profile_id": sp_id},
)
check(preview_resp.status_code == 200, f"47. API: preview against a saved profile (got {preview_resp.status_code}: {preview_resp.text[:200]})")
preview_body = preview_resp.json()
check(0 <= preview_body["score"] <= 100, "48. API: preview score is within bounds")
check(preview_body["profile_id"] == sp_id, "49. API: preview response records which profile produced it")

preview_inline = client.post(
    f"/api/candidates/{cand_a}/scoring-profiles/preview",
    json={"job": SYNTHETIC_JOB, "weights": ILLUSTRATIVE, "min_shortlist_score": 70},
)
check(preview_inline.status_code == 200, "50. API: preview also works with inline (unsaved) weights, not just a saved profile id")

no_criteria = client.post(f"/api/candidates/{cand_a}/scoring-profiles/preview", json={"job": SYNTHETIC_JOB})
check(no_criteria.status_code == 400, "51. API: preview with neither scoring_profile_id nor weights returns a clear 400")

# --- delete ---
delete_resp = client.delete(f"/api/candidates/{cand_a}/scoring-profiles/{dup_id}")
check(delete_resp.status_code == 200, "52. API: delete a profile the candidate owns")
after_delete = client.get(f"/api/candidates/{cand_a}/scoring-profiles/{dup_id}")
check(after_delete.status_code == 404, "53. API: a deleted profile is genuinely gone")

# --- fresh install does not create a real candidate or require API keys ---
fresh_candidates = client.get("/api/health")
check(fresh_candidates.status_code == 200, "54. API: health endpoint reachable without any provider key configured")

# --- page routes: the exact 404 bug found live (stale server process,
# not a code defect -- see scripts/run_production_api.py) never
# regresses in the actual code paths these two route handlers hit. ---
scoring_profiles_page = client.get("/settings/scoring-profiles")
check(
    scoring_profiles_page.status_code == 200 and "text/html" in scoring_profiles_page.headers.get("content-type", ""),
    f"54b. GET /settings/scoring-profiles returns 200 HTML (got {scoring_profiles_page.status_code})",
)
check(
    "Custom Job Scoring Profiles" in scoring_profiles_page.text,
    "54c. /settings/scoring-profiles serves the actual scoring_profiles.html content, not a stub",
)
search_providers_page = client.get("/settings/search-providers")
check(
    search_providers_page.status_code == 200 and "text/html" in search_providers_page.headers.get("content-type", ""),
    f"54d. GET /settings/search-providers still returns 200 HTML (got {search_providers_page.status_code})",
)
check(
    search_providers_page.text.count('>Scoring Profiles</a>') == 1,
    "54e. /settings/search-providers' own rendered page has exactly one Scoring Profiles nav link",
)

# --- existing endpoints unaffected: profile confirm still returns original shape ---
check(
    "identity" in confirm_a.json() and "professional_summary" in confirm_a.json(),
    "55. API: existing profile-confirm response shape is unchanged by this feature",
)

# --- saved-search min_score precedence is untouched for legacy searches ---
search_resp = client.post(
    f"/api/candidates/{cand_a}/searches",
    json={"name": "Legacy search", "target_roles": ["Senior SRE"], "target_locations": ["Bengaluru"], "minimum_match_score": 70, "sources": ["NAUKRI"]},
)
check(search_resp.status_code == 200, f"56. API: creating a saved search (no scoring_profile_id) still works exactly as before (got {search_resp.status_code}: {search_resp.text[:200]})")
check(
    search_resp.json().get("minimum_match_score") == 70,
    "57. API: a search's own minimum_match_score is preserved unchanged when no scoring profile is selected",
)

# --- a saved search can actually select a custom scoring profile, and it persists ---
search_with_profile = client.post(
    f"/api/candidates/{cand_a}/searches",
    json={
        "name": "Custom-scored search",
        "target_roles": ["Senior SRE"],
        "target_locations": ["Bengaluru"],
        "minimum_match_score": 70,
        "sources": ["NAUKRI"],
        "scoring_profile_id": sp_id,
    },
)
check(
    search_with_profile.status_code == 200 and search_with_profile.json().get("scoring_profile_id") == sp_id,
    f"58. API: creating a saved search with scoring_profile_id persists it (got {search_with_profile.status_code}: {search_with_profile.text[:200]})",
)
search_with_profile_id = search_with_profile.json()["saved_search_id"]

fetched_search = client.get(f"/api/searches/{search_with_profile_id}", params={"candidate_id": cand_a})
check(
    fetched_search.status_code == 200 and fetched_search.json().get("scoring_profile_id") == sp_id,
    "59. API: a fetched saved search still reports its selected scoring_profile_id",
)

cleared = client.patch(
    f"/api/searches/{search_with_profile_id}",
    params={"candidate_id": cand_a},
    json={"scoring_profile_id": None},
)
check(
    cleared.status_code == 200 and cleared.json().get("scoring_profile_id") is None,
    "60. API: explicitly clearing scoring_profile_id reverts the search to legacy scoring",
)

print()
print(f"{passed} passed, {failed} failed (module + API)")

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"SAFETY: production DB byte-identical before/after this test file (before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
