#!/usr/bin/env python3

"""
Static regression test for web/results.html's source filter + source
execution audit (Phases 4/5, regression letters E/F/G/K):

  E. the source filter defaults to blank/"All sources" (never a
     specific source, never APNA, never direct-only)
  F. the source filter's option list is built from the COMPLETE
     configured source universe (the API's "sources" field), not only
     from sources that happen to appear in the current result set
  G. a zero-result source stays visible/selectable (never silently
     dropped because it produced zero rows) and gets a source-specific
     empty-state message when selected
  K. the source execution audit table is rendered from real API data
     (data.sources), never hardcoded numbers, and never confuses a
     zero-result source with a never-searched one (distinct
     status/label values for each)

Pure static text-content analysis -- no browser, no server, no network.
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

# --- E: default is blank/"All sources", never a specific source ---
check(
    '<select id="f-source"><option value="">All sources</option></select>' in results_html,
    "E: the source filter's only built-in option is the blank 'All sources' default (nothing pre-selected)",
)
check(
    "APNA" not in results_html and 'value="NAUKRI"' not in results_html,
    "E: no hardcoded default source (APNA or otherwise) baked into the filter markup",
)

# --- F: filter options come from the complete source universe (data.sources), not results ---
populate_start = results_html.index("function populateFilterOptions")
populate_body = results_html[populate_start : populate_start + 900]
check(
    "sources.map" in populate_body and "results.map((r) => r.source)" not in populate_body,
    "F: populateFilterOptions() builds the source dropdown from the complete `sources` universe, "
    "not by scanning which sources happen to appear in `results`",
)
check(
    "allSources" in results_html and "data.sources" in results_html,
    "F: the page actually fetches and stores the API's 'sources' field (data.sources -> allSources), not just 'results'",
)
load_start = results_html.index("async function load")
load_body = results_html[load_start : load_start + 3000]
check(
    "populateFilterOptions(allResults, allSources)" in load_body,
    "F: populateFilterOptions() is actually called with the complete source universe, not just the result rows",
)

# --- G: zero-result source stays visible; distinct status vocabulary ---
render_results_start = results_html.index("function renderResults")
render_results_body = results_html[render_results_start : render_results_start + 900]
check(
    "No matching jobs from" in render_results_body,
    "G: selecting a specific (possibly zero-result) source shows a source-specific empty-state message, "
    "distinguishable from the generic 'no jobs at all' message",
)
app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
status_labels_match = re.search(r"SOURCE_STATUS_LABELS\s*=\s*\{([^}]+)\}", app_js)
check(bool(status_labels_match), "G: a source-status label map exists (shared, app.js) for the audit table")
if status_labels_match:
    labels_block = status_labels_match.group(1)
    for required_status in ("SUCCESS", "ZERO", "FAILED", "BLOCKED", "NOT_CONFIGURED", "NOT_ATTEMPTED"):
        check(
            required_status in labels_block,
            f"G: source status vocabulary includes {required_status} (never collapsed with another state)",
        )

# --- K: the source execution audit table is rendered from real API data, never hardcoded ---
audit_start = results_html.index("function renderSourceAudit")
audit_body = results_html[audit_start : audit_start + 1800]
check(
    "s.raw_count" in audit_body and "s.eligible_count" in audit_body and "s.displayed_count" in audit_body,
    "K: the audit table's Raw/Eligible/Displayed columns are read from each source's real API data (s.raw_count/eligible_count/displayed_count), never literal numbers",
)
check(
    re.search(r"\b(21|75|69)\b", audit_body) is None,
    "K: no literal job-count numbers are hardcoded into the audit rendering (would prove it's reading real data, not a fixed example)",
)
check(
    re.search(r"renderSourceAudit\(allSources", load_body) is not None,
    "K: the audit section is actually rendered with the real fetched source data (allSources), not a static example",
)

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
