#!/usr/bin/env python3

"""
Phase 11 regression tests for the Hirist company/title ambiguity fix,
using the REAL captured pages from this phase's controlled live
validation (data/reports/hirist_phase11_captures/ -- 1 listing page +
10 detail pages, fetched via the existing Playwright bridge, see
data/reports/phase11_public_multisource_completion.md for the full
request log). No live network/browser call is made by this file --
every test reads the already-saved capture.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAPTURES_DIR = ROOT / "data" / "reports" / "hirist_phase11_captures"
sys.path.insert(0, str(ROOT / "scripts"))

from hirist_parser import (
    _find_item_list_json_ld,
    _parse_job_entry_title_only,
    parse_job_detail_json_ld,
)

failures = []


def check(condition, message):
    if condition:
        print(f"PASS: {message}")
    else:
        failures.append(message)
        print(f"FAIL: {message}")


listing_html = (CAPTURES_DIR / "listing_devops_sre.html").read_text(encoding="utf-8")
item_list = _find_item_list_json_ld(listing_html)
check(item_list is not None, "1. real listing page's ItemList JSON-LD is found")
elements = item_list["itemListElement"]
check(len(elements) == 20, f"1. real listing page has 20 entries (got {len(elements)})")

title_url_entries = [_parse_job_entry_title_only(e) for e in elements]
check(all(e is not None for e in title_url_entries), "2. every one of the 20 real entries yields a title+job_url via _parse_job_entry_title_only() (none dropped -- unlike the old flat-name-split path, which silently dropped 10/20)")

no_delimiter_entry = next(e for e in title_url_entries if e["title"] == "Lead DevOps Engineer")
check(no_delimiter_entry is not None, "2. an entry whose title has ZERO ' - ' delimiters (impossible to split under the old heuristic) is still captured with its correct title")

expected_companies = {
    1: "UST", 2: "Capgemini Technology Services", 3: "Verint", 4: "Verint",
    5: "Vedantu Innovations", 6: "Capgemini Technology Services", 7: "Hitachi Solutions",
    8: "Verified Company", 9: "Cargill", 10: "Hostment",
}
all_correct = True
for i in range(1, 11):
    detail_html = (CAPTURES_DIR / f"detail_{i}.html").read_text(encoding="utf-8")
    result = parse_job_detail_json_ld(detail_html)
    if result is None or result["company"] != expected_companies[i]:
        all_correct = False
        print(f"  MISMATCH detail_{i}: expected {expected_companies[i]!r}, got {result['company'] if result else None!r}")
check(all_correct, "3. parse_job_detail_json_ld() correctly extracts the company for all 10 real detail-page captures (10/10), including the confidential/anonymized 'Verified Company' placeholder and a company absent from the listing title entirely ('Hostment')")

# The "Verified Company" case is Hirist's OWN honest anonymization
# label, not a parsing failure -- must be preserved verbatim, never
# treated as UNKNOWN or re-guessed from the title.
detail_8 = parse_job_detail_json_ld((CAPTURES_DIR / "detail_8.html").read_text(encoding="utf-8"))
check(detail_8["company"] == "Verified Company", "4. a genuinely employer-anonymized listing's company value is preserved verbatim, never overwritten or guessed")

# Malformed/absent JobPosting must fail closed, never fabricate.
check(parse_job_detail_json_ld("<html><body>no json-ld here</body></html>") is None, "5. a detail page with no JobPosting JSON-LD returns None (fails closed)")
check(parse_job_detail_json_ld('<script type="application/ld+json">{"@type":"JobPosting"}</script>') is None, "5. a JobPosting with no hiringOrganization.name returns None rather than fabricating a company")

print()
print(f"{len(elements) and '20 listing entries + 10 detail pages'} validated, {len(failures)} failed")
if failures:
    sys.exit(1)
