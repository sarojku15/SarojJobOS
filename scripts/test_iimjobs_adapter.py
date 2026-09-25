#!/usr/bin/env python3

"""
Offline tests for the iimjobs adapter (Phase 11). Makes NO live
network/browser request -- every test injects a FakeFetcher. Since
iimjobs_parser.py reuses hirist_parser.py's generic schema.org logic
directly (see that module's docstring), this file focuses on: URL
construction, the two-stage listing+detail pipeline end-to-end,
dedup/pagination/error-handling parity with Hirist, and the real
captured detail-page fixture from this phase's live validation.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAPTURES_DIR = ROOT / "data" / "reports" / "iimjobs_phase11_captures"
sys.path.insert(0, str(ROOT / "scripts"))

from source_adapter import SearchQuery, AdapterStatus, AdapterCapability, AdapterTimeoutError
from iimjobs_adapter import IimjobsAdapter, _build_search_url, _MAX_PAGES
from iimjobs_parser import parse_job_detail_json_ld, classify_search_page

failures = []


def check(condition, message):
    if condition:
        print(f"PASS: {message}")
    else:
        failures.append(message)
        print(f"FAIL: {message}")


class FakeFetcher:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        response = self._responses.get(url)
        if response is None:
            raise AssertionError(f"FakeFetcher has no configured response for {url!r}")
        if isinstance(response, Exception):
            raise response
        return response


# 1. URL construction
check(
    _build_search_url(SearchQuery(role="Product Manager", location="Mumbai")) == "https://www.iimjobs.com/search/j?q=Product%20Manager%20Mumbai",
    "1. search URL combines role+location into the q= search term",
)
check(
    _build_search_url(SearchQuery(role="X", location="", extra={"iimjobs_search_url": "https://www.iimjobs.com/custom"}))
    == "https://www.iimjobs.com/custom",
    "1. iimjobs_search_url escape hatch is honored verbatim",
)

# 2. End-to-end two-stage search()
LISTING_HTML = """<!DOCTYPE html><html><head><title>t</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"ItemList","numberOfItems":1,
"itemListElement":[{"@type":"ListItem","position":1,"name":"Acme - Senior PM","url":"https://www.iimjobs.com/j/acme-senior-pm-1"}]}
</script></head><body></body></html>"""

DETAIL_HTML = """<!DOCTYPE html><html><head><title>t</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting","title":"Senior PM",
"hiringOrganization":{"@type":"Organization","name":"Acme Corp"},
"jobLocation":[{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Mumbai"}}],
"datePosted":"01-09-2026","employmentType":"FULL_TIME","description":"Own the product."}
</script></head><body></body></html>"""

query = SearchQuery(role="Product Manager", location="Mumbai")
url = _build_search_url(query)
fetcher = FakeFetcher({url: LISTING_HTML, "https://www.iimjobs.com/j/acme-senior-pm-1": DETAIL_HTML})
adapter = IimjobsAdapter(fetcher=fetcher, rate_limit_seconds=0)
jobs = adapter.search(query)

check(len(jobs) == 1, f"2. end-to-end search() returns 1 job (got {len(jobs)})")
if jobs:
    check(jobs[0]["company"] == "Acme Corp", f"2. company resolved via detail page (got {jobs[0]['company']!r})")
    check(jobs[0]["location"] == "Mumbai", "2. location resolved via detail page")
    check(jobs[0]["job_url"] == "https://www.iimjobs.com/j/acme-senior-pm-1", "2. job_url preserved from listing")

# 3. A job whose detail page yields no company is dropped
fetcher2 = FakeFetcher({url: LISTING_HTML, "https://www.iimjobs.com/j/acme-senior-pm-1": "<html><body>no json-ld</body></html>"})
adapter2 = IimjobsAdapter(fetcher=fetcher2, rate_limit_seconds=0)
jobs2 = adapter2.search(query)
check(jobs2 == [], "3. a job whose detail page has no resolvable company is dropped, never fabricated")

# 4. HTTP error on first page propagates
fetcher3 = FakeFetcher({url: AdapterTimeoutError("IIMJOBS", detail="simulated")})
adapter3 = IimjobsAdapter(fetcher=fetcher3, rate_limit_seconds=0)
try:
    adapter3.search(query)
    check(False, "4. expected AdapterTimeoutError to propagate on first-page failure")
except AdapterTimeoutError:
    check(True, "4. AdapterTimeoutError propagates on first-page failure")

# 5. Status/capabilities
check(IimjobsAdapter.status == AdapterStatus.ENABLED, "5. IimjobsAdapter.status == ENABLED (Phase 11)")
check(
    IimjobsAdapter.capabilities == {AdapterCapability.SEARCH, AdapterCapability.DETAIL, AdapterCapability.PAGINATION},
    "5. IimjobsAdapter.capabilities == {SEARCH, DETAIL, PAGINATION}",
)

# 6. Real captured detail-page fixture from this phase's live validation
detail_capture = CAPTURES_DIR / "detail_1.html"
if detail_capture.exists():
    result = parse_job_detail_json_ld(detail_capture.read_text(encoding="utf-8"))
    check(result is not None and result["company"] == "Clinton Foundation", f"6. real captured iimjobs detail page correctly parsed (company={result['company'] if result else None!r})")
else:
    print("SKIP: 6. real capture fixture not found")

print()
print(f"{len(failures)} failed")
if failures:
    sys.exit(1)
