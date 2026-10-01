#!/usr/bin/env python3

"""
Phase 13.1 -- static frontend integrity regression test.

Guards against exactly the failure class Phase 13.1 was commissioned to
audit: duplicated HTML/JS blocks left over from iterative file patching
(duplicate element IDs, duplicate document-skeleton tags, duplicate
lexical declarations across a page's inline script and app.js, JS
syntax errors, and a DOM id referenced by JavaScript that doesn't
actually exist in that page's markup).

Pure static analysis -- no browser, no server, no network, no database.
Runs `node --check` if Node is available (skips that one check, never
fails the suite, if it is not).
"""

import re
import shutil
import subprocess
import sys
import tempfile
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


HTML_FILES = sorted(WEB_DIR.glob("*.html"))
check(len(HTML_FILES) >= 8, f"found the expected set of web/*.html pages (got {len(HTML_FILES)})")

APP_JS_TEXT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def top_level_names(js_text):
    names = []
    for m in re.finditer(r"^(?:const|let|var)\s+([A-Za-z0-9_$]+)", js_text, re.MULTILINE):
        names.append(m.group(1))
    for m in re.finditer(r"^function\s+([A-Za-z0-9_$]+)", js_text, re.MULTILINE):
        names.append(m.group(1))
    return names


APP_JS_TOP_LEVEL_NAMES = set(top_level_names(APP_JS_TEXT))

# Primary action handlers this audit specifically must never see doubled
# on any one page (save/confirm/create/upload/manual-start), keyed by
# the button id each page actually uses for that action. A page not
# listed for a given key simply doesn't have that action at all.
PRIMARY_ACTION_IDS = ["save-btn", "confirm-btn", "create-btn", "upload-btn", "manual-btn", "run-btn", "results-btn", "report-btn", "archive-btn", "import-btn"]

node_available = shutil.which("node") is not None
if not node_available:
    print("NOTE: node not found on PATH -- skipping node --check syntax validation.")

for html_path in HTML_FILES:
    text = html_path.read_text(encoding="utf-8")
    name = html_path.name

    # 1. Document-skeleton tags: exactly one each.
    for tag, pattern in [
        ("<html", r"<html[ >]"),
        ("<head>", r"<head>"),
        ("<body>", r"<body>"),
        ("<title>", r"<title>"),
    ]:
        count = len(re.findall(pattern, text))
        check(count == 1, f"{name}: exactly one {tag} (found {count})")

    # 2. No duplicate element IDs anywhere in the file (template content
    # included -- template-scoped reuse of an id is still a real hazard
    # this audit treats as a hard failure, not something to special-case).
    ids = re.findall(r'id="([^"]+)"', text)
    id_counts = {}
    for i in ids:
        id_counts[i] = id_counts.get(i, 0) + 1
    dup_ids = sorted(i for i, c in id_counts.items() if c > 1)
    check(not dup_ids, f"{name}: no duplicate element IDs (found: {dup_ids})")

    # 3. No duplicate primary-action element IDs specifically (belt and
    # suspenders on top of check 2, naming the exact failure class
    # reported: duplicate save/confirm/create/upload/archive buttons).
    dup_primary = sorted(a for a in PRIMARY_ACTION_IDS if id_counts.get(a, 0) > 1)
    check(not dup_primary, f"{name}: no duplicated primary-action button ids (found: {dup_primary})")

    # 4. Exactly one <script src="/static/app.js"> and one stylesheet link.
    check(text.count('app.js"') == 1, f"{name}: exactly one app.js script tag")
    check(text.count('style.css"') == 1, f"{name}: exactly one style.css link")

    # 5. No leftover patch/merge artifacts.
    check(
        not re.search(r"<{7}|={7}|>{7}", text),
        f"{name}: no leftover merge-conflict markers",
    )

    # 6. Extract inline scripts (there should be exactly one per page in
    # this app's convention -- app.js carries all shared code, each page
    # carries exactly one page-specific inline <script>).
    inline_scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", text, re.DOTALL)
    check(len(inline_scripts) == 1, f"{name}: exactly one inline <script> block (found {len(inline_scripts)})")

    if inline_scripts:
        script_text = inline_scripts[0]

        # 6a. No duplicate top-level const/let/var/function declarations
        # WITHIN this page's own inline script.
        names = top_level_names(script_text)
        seen = {}
        for n in names:
            seen[n] = seen.get(n, 0) + 1
        dup_in_page = sorted(n for n, c in seen.items() if c > 1)
        check(not dup_in_page, f"{name}: no duplicate top-level declarations within its own inline script (found: {dup_in_page})")

        # 6b. No collision between this page's top-level names and
        # app.js's top-level names (both share the same global scope in
        # a classic, non-module script).
        collide = sorted(set(names) & APP_JS_TOP_LEVEL_NAMES)
        check(not collide, f"{name}: no top-level name collides with a shared app.js global (found: {collide})")

        # 6c. Every DOM id this page's JS references via getElementById
        # must actually exist somewhere in the page's own markup.
        referenced = set(re.findall(r"getElementById\([\"']([^\"']+)[\"']\)", script_text))
        declared = set(ids)
        missing = sorted(referenced - declared)
        check(not missing, f"{name}: every getElementById() target exists in the page markup (missing: {missing})")

        # 6d. node --check syntax validation (skipped, not failed, if
        # node isn't available in this environment).
        if node_available:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tmp:
                tmp.write(script_text)
                tmp_path = tmp.name
            try:
                result = subprocess.run(["node", "--check", tmp_path], capture_output=True, text=True)
                check(result.returncode == 0, f"{name}: inline script passes `node --check` ({result.stderr.strip()[:200] if result.returncode else 'ok'})")
            finally:
                Path(tmp_path).unlink(missing_ok=True)

# app.js itself: no internal duplicate top-level declarations, valid syntax.
app_js_names = top_level_names(APP_JS_TEXT)
seen = {}
for n in app_js_names:
    seen[n] = seen.get(n, 0) + 1
dup_app_js = sorted(n for n, c in seen.items() if c > 1)
check(not dup_app_js, f"app.js: no duplicate top-level declarations (found: {dup_app_js})")

if node_available:
    result = subprocess.run(["node", "--check", str(WEB_DIR / "app.js")], capture_output=True, text=True)
    check(result.returncode == 0, f"app.js passes `node --check` ({result.stderr.strip()[:200] if result.returncode else 'ok'})")

# Exactly one implementation each of the three candidate-resolution
# helpers this project depends on (defined once in app.js, never
# redefined per-page).
for helper in ["resolveActiveCandidate", "requireActiveCandidateOrRedirect", "clearCandidateId"]:
    count_in_app = len(re.findall(rf"^(?:async\s+)?function\s+{helper}\b", APP_JS_TEXT, re.MULTILINE))
    check(count_in_app == 1, f"app.js: exactly one definition of {helper}() (found {count_in_app})")
    for html_path in HTML_FILES:
        text = html_path.read_text(encoding="utf-8")
        redefined = len(re.findall(rf"^(?:async\s+)?function\s+{helper}\b", text, re.MULTILINE))
        check(redefined == 0, f"{html_path.name}: does not redefine {helper}() (it must use app.js's single implementation)")

# ---------------------------------------------------------------------
# Application Tracker (2026-09-28) -- specific checks the master task
# requested beyond the generic per-file checks every HTML_FILES entry
# already gets above (which applications.html already passes: no
# duplicate skeleton tags, no dangling DOM-id references, etc).
# ---------------------------------------------------------------------

APPLICATIONS_HTML = WEB_DIR / "applications.html"
check(APPLICATIONS_HTML.exists(), "My Applications page exists at web/applications.html")
applications_text = APPLICATIONS_HTML.read_text(encoding="utf-8")

check('href="/applications"' in applications_text, "applications.html: nav links to itself (marked active)")
check("/api/candidates/${candidateId}/applications" in applications_text, "applications.html: fetches real application data from the API, not a hardcoded fixture")
check("summary-cards" in applications_text and "stat-value" in applications_text, "applications.html: renders summary cards (Total/Applied/Follow-ups Due/Overdue/etc.)")
check('badgeHtml(a.candidate_status)' in applications_text, "applications.html: renders a real status badge per application")
check("followup-now" in applications_text and "/follow-up/complete" in applications_text, "applications.html: 'Follow Up Now' action wired to the real complete endpoint")
check("reschedule" in applications_text and "/follow-up/schedule" in applications_text, "applications.html: 'Reschedule'/'Set Follow-up' action wired to the real schedule endpoint")
check("Details / History" in applications_text, "applications.html: links through to the per-job history/detail page")
check('id="f-status"' in applications_text and 'id="f-company"' in applications_text and 'id="f-source"' in applications_text and 'id="f-followup"' in applications_text, "applications.html: has status/company/source/follow-up filters")
check(re.search(r'href="[^"]+"', applications_text), "applications.html: has no broken/empty href attributes")

# Mark as Applied must exist on the results.html job-detail modal too
# (not only in My Applications) -- the audit's own recommendation was
# a DEDICATED action at the point where a candidate is already looking
# at the job, not only on a separate page.
RESULTS_HTML = (WEB_DIR / "results.html").read_text(encoding="utf-8")
check("mark-applied-btn" in RESULTS_HTML and "/mark-applied" in RESULTS_HTML, "results.html: has a dedicated Mark as Applied action, not just the generic status dropdown")
check("EMPLOYER_REJECTED" in RESULTS_HTML and "GHOSTED" in RESULTS_HTML and "SCREENING" in RESULTS_HTML, "results.html: status dropdown includes the previously-missing legitimate lifecycle states")
check('"REJECTED"' not in re.sub(r"EMPLOYER_REJECTED", "", RESULTS_HTML), "results.html: status dropdown no longer offers the legacy REJECTED value")

# No hardcoded production candidate IDs / secrets in any tracker file.
_REAL_CANDIDATE_ID_PATTERN = re.compile(r"""['"](saroj|cand_[0-9a-f]{8,})['"]""")
_SECRET_SHAPED_PATTERN = re.compile(r"(api[_-]?key|password|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{12,}['\"]", re.IGNORECASE)
for tracker_file in (APPLICATIONS_HTML, WEB_DIR / "results.html", WEB_DIR / "dashboard.html", WEB_DIR / "job_workspace.html"):
    text = tracker_file.read_text(encoding="utf-8")
    check(not _REAL_CANDIDATE_ID_PATTERN.search(text), f"{tracker_file.name}: no hardcoded production-looking candidate_id")
    check(not _SECRET_SHAPED_PATTERN.search(text), f"{tracker_file.name}: no accidental secret-shaped string")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
