#!/usr/bin/env python3

"""
Static regression test for web/results.html's shortlist/application
status UI (item 7's confirmed test gap: the audit found the UI itself
genuinely built -- a real status <select> + Update button wired to
updateJobStatus() -- contradicting README.md's stale "No UI button for
it yet (API only)" claim, but with zero test coverage anywhere).

Guards:
  1. The status-select dropdown exists and is populated from
     JOB_STATUS_OPTIONS, including SHORTLISTED/READY_FOR_APPROVAL/
     APPROVED/APPLIED -- the statuses item 7 explicitly asked for.
  2. The Update button is wired to a real click handler that calls
     updateJobStatus(), not a dead/unwired element.
  3. updateJobStatus() PATCHes the real, existing, ownership-scoped
     endpoint (/api/candidates/{candidateId}/jobs/{job_id}/status) --
     never a second/parallel status-write endpoint, never candidate B's
     job addressed via a hardcoded id.
  4. The candidate id used is ALWAYS resolveActiveCandidate()'s result
     -- never a literal string -- so ownership can't be bypassed from
     the client side.
  5. A successful update re-renders the results list (renderResults())
     so the change is visibly reflected, not just silently written.
  6. No auto-submit anywhere in this flow: the UI only ever PATCHes a
     status a human explicitly selected and clicked Update for; no
     code path calls this on page load or on a timer.
  7. The backend's own safety gate (APPROVED required before
     APPLICATION_STARTED/APPLIED) is never bypassed or duplicated
     client-side -- results.html has no client-side gate logic at all,
     the server (application_lifecycle.py) remains the sole authority.

Pure static text-content analysis -- no browser, no server, no
network -- matching this project's existing convention for frontend
correctness tests (see test_results_source_filter_frontend.py,
test_frontend_integrity.py). A genuine live-browser click-through of
this exact flow was also performed once, manually, against the running
dev server as part of this session's final live-proof verification
(see the session's final report) -- this file is the permanent,
fast, offline regression guard that runs on every suite invocation.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

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


results_html = (WEB_DIR / "results.html").read_text(encoding="utf-8")

# --- 1. the dropdown exists, populated from JOB_STATUS_OPTIONS ---
check('id="status-select"' in results_html, "1. status-select dropdown element exists in results.html")
options_match = re.search(r"const JOB_STATUS_OPTIONS\s*=\s*\[(.*?)\];", results_html, re.DOTALL)
check(options_match is not None, "1. JOB_STATUS_OPTIONS is defined")
if options_match:
    options_text = options_match.group(1)
    for required in ("SHORTLISTED", "READY_FOR_APPROVAL", "APPROVED", "APPLICATION_STARTED", "APPLIED"):
        check(f'"{required}"' in options_text, f"1. JOB_STATUS_OPTIONS includes {required}")

# --- 2. the Update button exists and is wired to updateJobStatus() ---
check('id="status-update-btn"' in results_html, "2. status-update-btn element exists")
check(
    re.search(r'getElementById\("status-update-btn"\)\.addEventListener\("click",\s*\(\)\s*=>\s*updateJobStatus\(', results_html) is not None,
    "2. status-update-btn's click handler calls updateJobStatus() (not a dead/unwired button)",
)

# --- 3. updateJobStatus() calls the real, existing endpoint ---
update_fn_match = re.search(r"async function updateJobStatus\(r, idx\)\s*\{(.*?)\n\}", results_html, re.DOTALL)
check(update_fn_match is not None, "3. updateJobStatus() function exists")
if update_fn_match:
    fn_body = update_fn_match.group(1)
    check(
        "/api/candidates/${candidateId}/jobs/${r.job_id}/status" in fn_body,
        "3. updateJobStatus() PATCHes the real ownership-scoped endpoint /api/candidates/{candidateId}/jobs/{job_id}/status",
    )
    check('method: "PATCH"' in fn_body, "3. the call uses PATCH (matches api/main.py's @app.patch route, never a second write path)")
    check("status: newStatus" in fn_body, "3. the request body carries exactly the human-selected status, nothing fabricated")

    # --- 4. candidate id always comes from resolveActiveCandidate() ---
    check("await resolveActiveCandidate()" in fn_body, "4. candidateId is resolved via resolveActiveCandidate(), never a literal/hardcoded id")
    check(not re.search(r'candidates/(cand_[a-zA-Z0-9_]+|["\'][a-zA-Z0-9_-]+["\'])/jobs', fn_body), "4. no hardcoded candidate_id anywhere in the PATCH call (would bypass ownership)")

    # --- 5. a successful update re-renders the list ---
    check("renderResults()" in fn_body, "5. a successful update calls renderResults() so the change is visibly reflected")

    # --- 6. no auto-submit: updateJobStatus is only ever invoked from
    #     the button's click handler, never from load()/setInterval/etc ---
    all_call_sites = re.findall(r"updateJobStatus\(", results_html)
    # Exactly 2: the function's own `async function updateJobStatus(` definition
    # and the one click-handler call site checked in #2 above.
    check(len(all_call_sites) == 2, f"6. updateJobStatus() is called from exactly one place (the button click handler), never automatically -- found {len(all_call_sites)} occurrences")
    check("setInterval" not in results_html and "setTimeout" not in results_html, "6. no timer-based automatic status changes anywhere in results.html")

# --- 7. no client-side approval-gate duplication: results.html must
#     not itself decide whether APPLICATION_STARTED/APPLIED is allowed
#     -- that stays exclusively server-side in application_lifecycle.py ---
check(
    not re.search(r"if\s*\(.*APPROVED.*\)\s*\{[^}]*(APPLICATION_STARTED|APPLIED)", results_html, re.DOTALL),
    "7. no client-side re-implementation of the APPROVED-before-APPLIED safety gate (server-only, per application_lifecycle.py)",
)

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
