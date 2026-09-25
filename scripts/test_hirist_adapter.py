#!/usr/bin/env python3

"""
Regression tests for the Phase 6 Step 3 offline Hirist adapter
implementation (scripts/hirist_adapter.py, scripts/hirist_parser.py).

Makes NO live network/browser request of any kind, ever. Every test
that exercises HiristAdapter.search()/.health_check() injects a
FakeFetcher -- the real, live-capable HiristFetcher (Phase 6 Step 4,
scripts/hirist_fetcher.py) is never invoked here. Fixtures are read
from data/fixtures/hirist/*.html -- all SYNTHETIC (see each fixture's
own header comment), none are real captured Hirist pages.

Covers, at minimum, the 14 scenarios explicitly requested for Phase 6
Step 3:
  1. valid ItemList with jobs
  2. empty ItemList
  3. malformed JSON-LD
  4. missing JSON-LD
  5. missing optional fields
  6. malformed job URL
  7. duplicate jobs
  8. rel=next pagination
  9. pagination loop protection
 10. maximum-page protection
 11. HTTP error handling
 12. parser never invents unsupported fields
 13. adapter remains NOT_ENABLED
 14. default sources remain ['NAUKRI']
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "data" / "fixtures" / "hirist"
sys.path.insert(0, str(ROOT / "scripts"))

from source_adapter import (
    SearchQuery,
    AdapterStatus,
    AdapterCapability,
    AdapterBlockedError,
    AdapterTimeoutError,
)
from hirist_adapter import HiristAdapter, _build_search_url, _MAX_PAGES
from hirist_fetcher import HiristFetcher
from hirist_parser import classify_search_page, HiristPageState
import search_profile
import source_registry


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _read_fixture(name):
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class FakeFetcher:
    """
    Injectable fake fetcher: maps exact URLs to fixture content (or a
    raised exception), never touches the network. Mirrors this
    project's existing FakeFetcher convention already used for Naukri
    tests.
    """

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


# ---------------------------------------------------------------------
# 1. Valid ItemList with jobs
# ---------------------------------------------------------------------

def test_valid_itemlist_with_jobs(failures):
    """
    Phase 11: company/location/posted_date now come from each job's own
    DETAIL page (schema.org JobPosting JSON-LD), never from the
    listing page's ambiguous flat "name" string or its
    (never-live-observed) nested-item shape -- see
    hirist_adapter.HiristAdapter.search()'s docstring and
    hirist_parser.parse_job_detail_json_ld()'s docstring for the full
    evidence trail (10/10 real detail pages correctly parsed).
    """
    query = SearchQuery(role="Senior DevOps Engineer", location="Bengaluru")
    url = _build_search_url(query)

    page2_url = "https://www.hirist.tech/search/senior-devops-engineer-jobs?page=2"
    fetcher = FakeFetcher({
        url: _read_fixture("phase11_listing_page1.html"),
        page2_url: _read_fixture("phase11_listing_page2.html"),
        "https://www.hirist.tech/j/verint-senior-devops-engineer-1001": _read_fixture("detail_verint_senior_devops.html"),
        "https://www.hirist.tech/job-detail/lead-platform-engineer-12345": _read_fixture("detail_lead_platform_engineer.html"),
        "https://www.hirist.tech/j/acme-corp-staff-sre-1002": _read_fixture("detail_acme_staff_sre.html"),
    })

    adapter = HiristAdapter(fetcher=fetcher, rate_limit_seconds=0)
    jobs = adapter.search(query)

    if len(jobs) != 3:
        return _fail(failures, f"expected 3 jobs (2 from page1 + 1 from page2), got {len(jobs)}: {jobs}")

    companies = {job["company"] for job in jobs}
    if "Verint" not in companies or "Example Systems Pvt Ltd" not in companies or "Acme Corp" not in companies:
        return _fail(failures, f"expected companies not all present: {companies}")

    nested_job = next(j for j in jobs if j["company"] == "Example Systems Pvt Ltd")
    if nested_job["job_url"] != "https://www.hirist.tech/job-detail/lead-platform-engineer-12345":
        return _fail(failures, f"job_url not extracted correctly: {nested_job['job_url']!r}")
    if nested_job["posted_date"] != "2026-09-18":
        return _fail(failures, f"detail-page posted_date not extracted correctly: {nested_job['posted_date']!r}")
    if nested_job["location"] != "Bengaluru":
        return _fail(failures, f"detail-page location not extracted correctly: {nested_job['location']!r}")

    print("PASS: 1 -> valid ItemList with jobs parsed correctly across 2 pages; company/location/posted_date correctly resolved via each job's own detail page")


# ---------------------------------------------------------------------
# 2. Empty ItemList
# ---------------------------------------------------------------------

def test_empty_itemlist(failures):
    fetcher = FakeFetcher({"https://x/empty": _read_fixture("empty_results.html")})
    classification = classify_search_page(_read_fixture("empty_results.html"), "https://x/empty")

    if classification.state != HiristPageState.VALID_EMPTY_RESULT:
        return _fail(failures, f"expected VALID_EMPTY_RESULT, got {classification.state}")
    if classification.jobs != []:
        return _fail(failures, f"expected zero jobs, got {classification.jobs}")

    print("PASS: 2 -> empty ItemList classified as VALID_EMPTY_RESULT with zero jobs")


# ---------------------------------------------------------------------
# 3. Malformed JSON-LD
# ---------------------------------------------------------------------

def test_malformed_json_ld(failures):
    classification = classify_search_page(_read_fixture("malformed_json_ld.html"), "https://x/malformed")

    if classification.state != HiristPageState.PARSE_FAILURE:
        return _fail(failures, f"expected PARSE_FAILURE for malformed JSON-LD, got {classification.state}")

    print("PASS: 3 -> malformed JSON-LD classified as PARSE_FAILURE")


# ---------------------------------------------------------------------
# 4. Missing JSON-LD
# ---------------------------------------------------------------------

def test_missing_json_ld(failures):
    classification = classify_search_page(_read_fixture("missing_json_ld.html"), "https://x/missing")

    if classification.state != HiristPageState.PARSE_FAILURE:
        return _fail(failures, f"expected PARSE_FAILURE for missing JSON-LD, got {classification.state}")

    print("PASS: 4 -> missing JSON-LD classified as PARSE_FAILURE")


# ---------------------------------------------------------------------
# 5. Missing optional fields
# ---------------------------------------------------------------------

def test_missing_optional_fields(failures):
    """The flat-name path (the one Phase 6 actually observed) never
    has salary/experience/description/etc -- confirms all of those
    default to empty string/list, never fabricated."""
    classification = classify_search_page(_read_fixture("phase11_listing_page1.html"), "https://x/page1")

    flat_job = next((j for j in classification.jobs if j["company"] == "Verint"), None)
    if flat_job is None:
        return _fail(failures, "flat-name job entry not found in classification result")

    for field_name in ("posted_date", "jd_text", "experience_required", "application_url"):
        if flat_job[field_name] != "":
            return _fail(failures, f"flat-name job's {field_name!r} was not empty: {flat_job[field_name]!r}")
    if flat_job["mandatory_skills"] != [] or flat_job["preferred_skills"] != []:
        return _fail(failures, "flat-name job's skill lists were not empty")

    print("PASS: 5 -> missing optional fields default to empty string/list, never fabricated")


# ---------------------------------------------------------------------
# 6. Malformed job URL
# ---------------------------------------------------------------------

def test_malformed_job_url(failures):
    classification = classify_search_page(_read_fixture("malformed_job_url.html"), "https://x/badurl")

    if classification.state != HiristPageState.VALID_RESULTS:
        return _fail(failures, f"expected VALID_RESULTS (entry still parseable via company+title), got {classification.state}")
    if len(classification.jobs) != 1:
        return _fail(failures, f"expected exactly 1 job, got {len(classification.jobs)}")

    job = classification.jobs[0]
    if job["company"] != "Broken URL Co" or job["title"] != "Cloud Engineer":
        return _fail(failures, f"company/title not extracted despite malformed url: {job}")
    if job["job_url"] != "":
        return _fail(failures, f"malformed job_url was not rejected, got {job['job_url']!r}")

    print("PASS: 6 -> malformed job URL left empty, does not invalidate the rest of the entry")


# ---------------------------------------------------------------------
# 7. Duplicate jobs
# ---------------------------------------------------------------------

def test_duplicate_jobs(failures):
    """Phase 11: dedup key is now job_url (the one field every listing
    entry is guaranteed to have before its detail page is even
    fetched) rather than (company, title) -- see
    HiristAdapter.search()'s seen_keys usage."""
    query = SearchQuery(role="Senior DevOps Engineer", location="Bengaluru")
    url = _build_search_url(query)
    fetcher = FakeFetcher({
        url: _read_fixture("phase11_duplicate_jobs.html"),
        "https://www.hirist.tech/j/verint-senior-devops-engineer-1001": _read_fixture("detail_verint_senior_devops.html"),
    })

    adapter = HiristAdapter(fetcher=fetcher, rate_limit_seconds=0)
    jobs = adapter.search(query)

    if len(jobs) != 1:
        return _fail(failures, f"expected exactly 1 job after in-call deduplication, got {len(jobs)}: {jobs}")
    if fetcher.calls.count("https://www.hirist.tech/j/verint-senior-devops-engineer-1001") != 1:
        return _fail(failures, "duplicate listing entries must fetch the detail page exactly once, not once per duplicate")

    print("PASS: 7 -> duplicate job_url entries within one page are deduplicated before any detail-page fetch")


# ---------------------------------------------------------------------
# 8. rel=next pagination
# ---------------------------------------------------------------------

def test_rel_next_pagination(failures):
    classification = classify_search_page(_read_fixture("phase11_listing_page1.html"), "https://www.hirist.tech/search/senior-devops-engineer-jobs?locations=Bengaluru")

    if classification.next_url != "https://www.hirist.tech/search/senior-devops-engineer-jobs?page=2":
        return _fail(failures, f"rel=next URL not extracted correctly: {classification.next_url!r}")

    # Safety check: a rel="next" link pointing off this project's one
    # observed host must never be followed.
    off_domain_classification = classify_search_page(
        _read_fixture("next_link_off_domain.html"),
        "https://www.hirist.tech/search/senior-devops-engineer-jobs",
    )
    if off_domain_classification.next_url is not None:
        return _fail(failures, f"an off-domain rel=next link was followed: {off_domain_classification.next_url!r}")

    print("PASS: 8 -> rel=\"next\" pagination link correctly extracted; an off-domain next link is never followed")


# ---------------------------------------------------------------------
# 9. Pagination loop protection
# ---------------------------------------------------------------------

def test_pagination_loop_protection(failures):
    page1_url = "https://www.hirist.tech/search/senior-devops-engineer-jobs?locations=Bengaluru"

    fetcher = FakeFetcher({
        page1_url: _read_fixture("phase11_pagination_self_loop.html"),
        "https://www.hirist.tech/j/loop-co-some-role-9001": _read_fixture("detail_loop_co_some_role.html"),
    })

    query = SearchQuery(role="Senior DevOps Engineer", location="Bengaluru")
    adapter = HiristAdapter(fetcher=fetcher, rate_limit_seconds=0)
    jobs = adapter.search(query)

    # The fixture's own rel="next" points straight back to page1_url --
    # search() must visit page1 exactly once (not loop) and return its
    # one job.
    if fetcher.calls.count(page1_url) != 1:
        return _fail(failures, f"page1 URL was fetched {fetcher.calls.count(page1_url)} times, expected exactly 1 (loop protection failed): {fetcher.calls}")
    if len(jobs) != 1:
        return _fail(failures, f"expected exactly 1 job, got {len(jobs)}: {jobs}")

    print("PASS: 9 -> a self-referencing rel=\"next\" link does not cause an infinite pagination loop")


# ---------------------------------------------------------------------
# 10. Maximum-page protection
# ---------------------------------------------------------------------

def test_maximum_page_protection(failures):
    """A pathological site that always returns a NEW, never-before-seen
    next URL (so loop protection alone would never stop it) must still
    be bounded by _MAX_PAGES."""
    base = "https://www.hirist.tech/search/senior-devops-engineer-jobs"

    def detail_url(n):
        return f"https://www.hirist.tech/j/page{n}-co-role{n}-{9100 + n}"

    def page_html(n):
        next_link = f'<link rel="next" href="{base}?page={n + 1}">'
        return f"""<!DOCTYPE html><html><head><title>t</title>{next_link}
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"ItemList","numberOfItems":1,
"itemListElement":[{{"@type":"ListItem","position":1,"name":"Page{n} Co - Role{n}","url":"{detail_url(n)}"}}]}}
</script></head><body></body></html>"""

    def detail_html(n):
        return f"""<!DOCTYPE html><html><head><title>t</title>
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"JobPosting","title":"Role{n}",
"hiringOrganization":{{"@type":"Organization","name":"Page{n} Co"}}}}
</script></head><body></body></html>"""

    class InfiniteFetcher:
        def __init__(self):
            self.call_count = 0

        def fetch(self, url):
            self.call_count += 1
            if url.startswith("https://www.hirist.tech/j/page"):
                n = int(url.rsplit("-", 1)[1]) - 9100
                return detail_html(n)
            # Determine page number from the URL (page 1 has no ?page=)
            if "?page=" in url:
                n = int(url.rsplit("=", 1)[1])
            else:
                n = 1
            return page_html(n)

    fetcher = InfiniteFetcher()
    query = SearchQuery(role="Senior DevOps Engineer", location="Bengaluru")
    adapter = HiristAdapter(fetcher=fetcher, rate_limit_seconds=0)
    jobs = adapter.search(query)

    # _MAX_PAGES listing fetches + one detail fetch per collected job
    # (bounded separately by _MAX_DETAIL_FETCHES_PER_QUERY, which is
    # >= _MAX_PAGES here since only 1 job/page is ever produced).
    expected_calls = _MAX_PAGES * 2
    if fetcher.call_count != expected_calls:
        return _fail(failures, f"fetcher was called {fetcher.call_count} times, expected exactly {expected_calls} ({_MAX_PAGES} listing + {_MAX_PAGES} detail)")
    if len(jobs) != _MAX_PAGES:
        return _fail(failures, f"expected {_MAX_PAGES} jobs (one per page up to the bound), got {len(jobs)}")

    print(f"PASS: 10 -> pagination is bounded at _MAX_PAGES={_MAX_PAGES} even against a site that never stops offering a next page")


# ---------------------------------------------------------------------
# 11. HTTP error handling
# ---------------------------------------------------------------------

def test_http_error_handling(failures):
    query = SearchQuery(role="Senior DevOps Engineer", location="Bengaluru")
    url = _build_search_url(query)

    # First-page failure must propagate.
    fetcher = FakeFetcher({url: AdapterTimeoutError("HIRIST", detail="simulated HTTP failure")})
    adapter = HiristAdapter(fetcher=fetcher, rate_limit_seconds=0)
    try:
        adapter.search(query)
        _fail(failures, "expected AdapterTimeoutError to propagate on a first-page HTTP failure")
        return
    except AdapterTimeoutError:
        pass

    # A later-page failure must stop pagination gracefully, keeping
    # already-collected results.
    page2_url = "https://www.hirist.tech/search/senior-devops-engineer-jobs?page=2"
    fetcher2 = FakeFetcher({
        url: _read_fixture("phase11_listing_page1.html"),
        page2_url: AdapterTimeoutError("HIRIST", detail="simulated HTTP failure on page 2"),
        "https://www.hirist.tech/j/verint-senior-devops-engineer-1001": _read_fixture("detail_verint_senior_devops.html"),
        "https://www.hirist.tech/job-detail/lead-platform-engineer-12345": _read_fixture("detail_lead_platform_engineer.html"),
    })
    adapter2 = HiristAdapter(fetcher=fetcher2, rate_limit_seconds=0)
    jobs = adapter2.search(query)
    if len(jobs) != 2:
        return _fail(failures, f"expected the 2 page-1 jobs to survive a page-2 HTTP failure, got {len(jobs)}")

    print("PASS: 11 -> HTTP failure on page 1 propagates; HTTP failure on a later page stops pagination gracefully, keeping earlier results")


# ---------------------------------------------------------------------
# 12. Parser never invents unsupported fields
# ---------------------------------------------------------------------

def test_parser_never_invents_unsupported_fields(failures):
    """Confirms the flat-name extraction path NEVER populates
    salary/experience/description/application_url/employment-type --
    none of those were ever observed alongside the flat 'name' field in
    Phase 6, and this project's explicit instruction is not to assume
    them."""
    entry = {"@type": "ListItem", "position": 1, "name": "Test Co - Test Title"}

    from hirist_parser import _parse_job_entry

    job = _parse_job_entry(entry)
    if job is None:
        return _fail(failures, "expected a valid job from a well-formed flat-name entry")

    never_invented_fields = ["posted_date", "jd_text", "experience_required", "application_url", "work_model", "location"]
    for field_name in never_invented_fields:
        if job[field_name] != "":
            return _fail(failures, f"flat-name path invented a value for {field_name!r}: {job[field_name]!r}")

    # A delimiter occurring zero or multiple times must be rejected,
    # never guessed.
    ambiguous_entry_zero = {"@type": "ListItem", "position": 1, "name": "No delimiter here"}
    ambiguous_entry_multi = {"@type": "ListItem", "position": 1, "name": "A - B - C"}

    if _parse_job_entry(ambiguous_entry_zero) is not None:
        return _fail(failures, "an entry with zero delimiter occurrences was not rejected")
    if _parse_job_entry(ambiguous_entry_multi) is not None:
        return _fail(failures, "an entry with multiple delimiter occurrences was not rejected (would require guessing a split point)")

    print("PASS: 12 -> parser never invents unsupported fields from the flat-name path, and rejects ambiguous delimiter cases rather than guessing")


# ---------------------------------------------------------------------
# 13. Adapter remains NOT_ENABLED
# ---------------------------------------------------------------------

def test_adapter_enabled_phase11(failures):
    """
    Phase 11: HiristAdapter is now AdapterStatus.ENABLED -- the
    company/title ambiguity that kept it NOT_ENABLED through Phases
    6-10 was fixed (two-stage listing+detail-page fetch, see
    HiristAdapter.search()'s docstring) and the full enablement gate
    was passed via a controlled, real, end-to-end live validation
    (data/reports/phase11_public_multisource_completion.md): 14 real
    requests, all status 200, zero blocks, 10/10 jobs correctly
    resolved. This test only verifies the class-level declaration --
    it makes no live call itself.
    """
    if HiristAdapter.status != AdapterStatus.ENABLED:
        return _fail(failures, f"HiristAdapter.status = {HiristAdapter.status}, expected ENABLED (Phase 11)")
    expected_caps = {AdapterCapability.SEARCH, AdapterCapability.DETAIL, AdapterCapability.PAGINATION}
    if HiristAdapter.capabilities != expected_caps:
        return _fail(failures, f"HiristAdapter.capabilities = {HiristAdapter.capabilities}, expected {expected_caps}")

    adapter = HiristAdapter()
    if not isinstance(adapter._fetcher, HiristFetcher):
        _fail(failures, f"HiristAdapter's default fetcher is not HiristFetcher: {type(adapter._fetcher).__name__}")

    print("PASS: 13 -> HiristAdapter is AdapterStatus.ENABLED with capabilities {SEARCH, DETAIL, PAGINATION}, per Phase 11's passed live-validation gate")


# ---------------------------------------------------------------------
# 14. Default sources remain ['NAUKRI']
# ---------------------------------------------------------------------

def test_default_sources_includes_hirist_phase11(failures):
    """
    Phase 11: now that HiristAdapter.status is genuinely ENABLED (see
    test 13), _default_sources() SHOULD include it -- this is
    search_profile.py's own designed, unmodified behavior ("every
    currently ENABLED, non-MOCK adapter... automatically includes
    future adapters once they are actually enabled"), not a new
    special case for Hirist.
    """
    # A subset check, not exact equality: _default_sources() also
    # includes any search-provider-backed source (*_SEARCH) that
    # happens to be ENABLED in THIS environment's .env -- genuinely
    # environment-dependent, and correctly so (see search_provider.py's
    # own root-cause fix ensuring .env is always loaded before this
    # point). This test's own concern is only that the 4 direct
    # sources are present, never that they are the ONLY ones.
    defaults = search_profile._default_sources()
    if not {"NAUKRI", "HIRIST", "IIMJOBS", "APNA"} <= set(defaults):
        return _fail(failures, f"_default_sources() = {defaults!r}, expected it to include at least {{'NAUKRI', 'HIRIST', 'IIMJOBS', 'APNA'}} now that Hirist/iimjobs are genuinely ENABLED")

    print(f"PASS: 14 -> search_profile._default_sources() includes {{'NAUKRI', 'HIRIST', 'IIMJOBS', 'APNA'}} (got {sorted(defaults)}) -- Hirist's now-real ENABLED status flows through the existing, unmodified default-source logic")


def main():
    print("HIRIST ADAPTER OFFLINE IMPLEMENTATION TEST (Phase 6 Step 3)")
    print("===============================================================")

    failures = []

    test_valid_itemlist_with_jobs(failures)
    test_empty_itemlist(failures)
    test_malformed_json_ld(failures)
    test_missing_json_ld(failures)
    test_missing_optional_fields(failures)
    test_malformed_job_url(failures)
    test_duplicate_jobs(failures)
    test_rel_next_pagination(failures)
    test_pagination_loop_protection(failures)
    test_maximum_page_protection(failures)
    test_http_error_handling(failures)
    test_parser_never_invents_unsupported_fields(failures)
    test_adapter_enabled_phase11(failures)
    test_default_sources_includes_hirist_phase11(failures)

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll Hirist adapter offline implementation tests passed.")


if __name__ == "__main__":
    main()
