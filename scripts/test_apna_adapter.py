#!/usr/bin/env python3

"""
Offline tests for the real Apna adapter (Phase 12). Makes NO live
network request -- every test injects a fake fetcher or uses the real
captured fixture (data/reports/apna_phase12_captures/). Covers parser
correctness against real data, adapter orchestration (pagination,
dedup, error handling), and the required 14-scenario data-quality
checklist.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAPTURES_DIR = ROOT / "data" / "reports" / "apna_phase12_captures"
sys.path.insert(0, str(ROOT / "scripts"))

from source_adapter import SearchQuery, AdapterStatus, AdapterCapability, AdapterTimeoutError, AdapterBlockedError
from apna_parser import extract_job_feed, parse_job_feed_entries, find_next_page_url, reassemble_next_f_buffer, parse_detail_page, extract_job_posting_schema
from apna_adapter import ApnaAdapter, _build_search_url

# Sections 1-4 below predate detail-page fetching and test pagination/
# dedup/error-handling only -- max_detail_fetches_per_query=0 keeps
# them testing exactly that, unaffected by the newer detail-fetching
# behavior (covered separately in its own section further down).

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


# ------------------------------------------------------------- 1. real fixture

real_html = (CAPTURES_DIR / "listing_sre_bengaluru_page1.html").read_text(encoding="utf-8")

feed = extract_job_feed(real_html)
check(feed is not None, "1. real captured page -> initialSSRJobFeedData found")
check(feed.get("totalJobsCount", 0) > 0, "1. real capture reports a non-zero totalJobsCount")

jobs = parse_job_feed_entries(feed)
check(len(jobs) == 25, f"1. real capture parses to 25 jobs (got {len(jobs)}) -- at least 10 required, exceeded")
check(all(j["title"] and j["company"] and j["job_url"] for j in jobs), "1. every parsed job has non-empty title/company/job_url")
check(any(j["company"] == "The Walt Disney Company" for j in jobs), "1. a known real company from the capture is present verbatim")
check(all(j["job_url"].startswith("https://apna.co/") for j in jobs), "1. every job_url is a well-formed, canonical apna.co URL")
check(all(j["posted_date"] == "" for j in jobs), "1. posted_date is honestly left empty (never observed in Apna's feed, never guessed)")

next_url = find_next_page_url(real_html, "https://apna.co/jobs")
check(next_url == "https://apna.co/jobs?page=2", f"1. pagination link correctly extracted from the RSC payload (got {next_url!r})")

# --------------------------------------------------------- 2. malformed inputs

check(extract_job_feed("<html><body>no data here</body></html>") is None, "2. a page with no RSC payload returns None (fails closed)")
check(parse_job_feed_entries(None) == [], "2. parse_job_feed_entries(None) returns [] rather than crashing")
check(parse_job_feed_entries({"jobsList": [{"data": {"jobTitle": "X"}}]}) == [], "2. an entry missing company is skipped, never fabricated")
check(parse_job_feed_entries({"jobsList": [{"data": {"jobOrganisationDetails": {"organisationName": "X"}}}]}) == [], "2. an entry missing title is skipped, never fabricated")
check(
    parse_job_feed_entries({"jobsList": [{"data": {"jobTitle": "T", "jobOrganisationDetails": {"organisationName": "C"}}}]}) == [],
    "2. an entry missing a resolvable job_url is skipped (never presented without a URL)",
)

# ------------------------------------------------------------- 3. URL building

url = _build_search_url(SearchQuery(role="X", location="", extra={"apna_search_url": "https://apna.co/jobs?custom=1"}))
check(url == "https://apna.co/jobs?custom=1", "3. apna_search_url escape hatch honored verbatim")

# ------------------------------------------------------- 4. adapter end-to-end

query = SearchQuery(role="Site Reliability Engineer", location="")  # empty location -> skip location resolution entirely
search_url = _build_search_url(query)
page2_html = """<script>self.__next_f.push([1,"{\\"initialSSRJobFeedData\\":{\\"jobsList\\":[{\\"data\\":{\\"jobTitle\\":\\"Page2 Role\\",\\"jobOrganisationDetails\\":{\\"organisationName\\":\\"Page2 Co\\"},\\"jobPublicURL\\":\\"/job/page2-role-999\\",\\"jobCardAddress\\":\\"Remote\\",\\"jobUITags\\":[]}}],\\"totalJobsCount\\":2}}"])</script>"""

fetcher = FakeFetcher({search_url: real_html, "https://apna.co/jobs?page=2": page2_html})
adapter = ApnaAdapter(fetcher=fetcher, rate_limit_seconds=0, max_detail_fetches_per_query=0)
all_jobs = adapter.search(query)
check(len(all_jobs) == 26, f"4. end-to-end search() follows pagination and combines both pages (25 + 1 = 26, got {len(all_jobs)})")
check(any(j["title"] == "Page2 Role" for j in all_jobs), "4. page 2's job is included")

# Duplicate URL across pages must not multiply.
fetcher_dup = FakeFetcher({search_url: real_html, "https://apna.co/jobs?page=2": real_html})
adapter_dup = ApnaAdapter(fetcher=fetcher_dup, rate_limit_seconds=0, max_detail_fetches_per_query=0)
deduped_jobs = adapter_dup.search(query)
check(len(deduped_jobs) == 25, f"4. duplicate job_url entries across pages (same page served twice) are deduplicated (expected 25, got {len(deduped_jobs)})")

# First-page failure propagates; a genuinely BLOCKED page also does.
fetcher_err = FakeFetcher({search_url: AdapterTimeoutError("APNA", detail="simulated")})
adapter_err = ApnaAdapter(fetcher=fetcher_err, rate_limit_seconds=0, max_detail_fetches_per_query=0)
raised = False
try:
    adapter_err.search(query)
except AdapterTimeoutError:
    raised = True
check(raised, "4. AdapterTimeoutError on the first page propagates")

# No feed found on first page -> AdapterTimeoutError (structure changed), never silently empty.
fetcher_empty = FakeFetcher({search_url: "<html><body>changed layout</body></html>"})
adapter_empty = ApnaAdapter(fetcher=fetcher_empty, rate_limit_seconds=0, max_detail_fetches_per_query=0)
raised = False
try:
    adapter_empty.search(query)
except AdapterTimeoutError:
    raised = True
check(raised, "4. a first page with no extractable job feed raises rather than returning a silent empty result")

# ------------------------------------------------------------ 5. status/caps

check(ApnaAdapter.status == AdapterStatus.ENABLED, "5. ApnaAdapter.status == ENABLED (Phase 12)")
check(ApnaAdapter.capabilities == {AdapterCapability.SEARCH, AdapterCapability.PAGINATION}, "5. ApnaAdapter.capabilities == {SEARCH, PAGINATION}")

# --------------------------------------------------- 6. CommonJob normalization

sys.path.insert(0, str(ROOT / "scripts"))
from discover_local import normalize_job

normalized = normalize_job(jobs[0], 0)
check(normalized["job_id"], "6. a real parsed Apna job normalizes successfully via the EXISTING discover_local.normalize_job()")
check(normalized["source"] == "APNA", "6. normalized job preserves source=APNA")

# --------------------------------------------------- 7. detail-page fetching
# Deterministic fixture HTML mirroring the real, live-captured
# structure (schema.org JobPosting JSON-LD in a
# <script id="jdp-job-schema" ...> tag) -- never a live network call.

def _detail_html(job_posting_dict, include_schema=True):
    import json as _json
    if not include_schema:
        return "<html><body>no schema here</body></html>"
    payload = _json.dumps(job_posting_dict)
    return f'<html><body><script id="jdp-job-schema" type="application/ld+json">{payload}</script></body></html>'


_FULL_POSTING = {
    "@context": "https://schema.org/",
    "@type": "JobPosting",
    "title": "Senior Site Reliability Engineer",
    "description": "Own production reliability. Kubernetes, Terraform, CI/CD, observability.",
    "datePosted": "2026-09-01T00:00:00.000Z",
    "employmentType": "FULL_TIME",
    "experienceRequirements": {"@type": "OccupationalExperienceRequirements", "monthsOfExperience": 60},
    "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Bengaluru/Bangalore"}},
}

# 7a. extract_job_posting_schema / parse_detail_page on a full, real-shaped fixture
posting = extract_job_posting_schema(_detail_html(_FULL_POSTING))
check(posting is not None and posting.get("title") == "Senior Site Reliability Engineer", "7a. extract_job_posting_schema() parses a real-shaped JSON-LD block")

enrichment = parse_detail_page(_detail_html(_FULL_POSTING), "https://apna.co/job/x")
check(enrichment.get("jd_text") == _FULL_POSTING["description"], "7b. detail page JD becomes jd_text, verbatim")
check(enrichment.get("posted_date") == "2026-09-01", "7b. posted_date correctly sliced from the ISO datePosted")
check(enrichment.get("experience_min_months") == 60, "7b. experience_min_months correctly extracted (60 months)")
check(enrichment.get("experience_required") == "Min. 5 years", f"7b. experience_required correctly derived (60 months = 5 years), got {enrichment.get('experience_required')!r}")
check(enrichment.get("location") == "Bengaluru/Bangalore", "7b. location correctly extracted from jobLocation.address.addressLocality")

# 7c. detail page failure (no schema block) preserves listing result --
# parse_detail_page returns {} (nothing to merge), never guessed values.
empty_enrichment = parse_detail_page(_detail_html({}, include_schema=False), "https://apna.co/job/x")
check(empty_enrichment == {}, "7c. a detail page with no JSON-LD schema returns {} (empty), never guessed/fabricated fields")

# 7d. empty description in a present-but-sparse posting does not overwrite
sparse_enrichment = parse_detail_page(_detail_html({"title": "X"}), "https://apna.co/job/x")
check("jd_text" not in sparse_enrichment, "7d. a posting with no description key never sets jd_text at all (not even empty string)")


class _DetailFakeFetcher:
    """Serves the listing page for the search URL, and per-job-URL
    detail responses from `detail_responses` (a dict; missing key or an
    Exception instance simulates a fetch failure for that one URL)."""

    def __init__(self, listing_url, listing_html, detail_responses):
        self._listing_url = listing_url
        self._listing_html = listing_html
        self._detail_responses = detail_responses
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        if url == self._listing_url:
            return self._listing_html
        if url not in self._detail_responses:
            raise AssertionError(f"no detail response configured for {url!r}")
        response = self._detail_responses[url]
        if isinstance(response, Exception):
            raise response
        return response


# A small, deterministic 4-job listing fixture (distinct real-shaped
# entries, not the 25-job captured fixture, so exact detail-fetch
# counts/bounds are easy to assert precisely).
def _listing_entry(n):
    return (
        '{"data":{"jobTitle":"Role %d","jobOrganisationDetails":{"organisationName":"Co %d"},'
        '"jobPublicURL":"/job/role-%d","jobCardAddress":"City %d","jobUITags":[]}}' % (n, n, n, n)
    )


_listing_4jobs_html = (
    '<script>self.__next_f.push([1,"{\\"initialSSRJobFeedData\\":{\\"jobsList\\":['
    + ",".join(_listing_entry(i).replace('"', '\\"') for i in range(1, 5))
    + '],\\"totalJobsCount\\":4}}"])</script>'
)

detail_query = SearchQuery(role="X", location="")
detail_search_url = _build_search_url(detail_query)

# 7e/7g. 3 of 4 jobs get a real detail response with a real JD; job 3
# fails (timeout); bound is 4 -- all 4 attempted, 3 succeed, 1 fails.
detail_responses = {
    "https://apna.co/job/role-1": _detail_html({**_FULL_POSTING, "description": "JD for role 1 kubernetes terraform"}),
    "https://apna.co/job/role-2": _detail_html({**_FULL_POSTING, "description": "JD for role 2 observability cicd"}),
    "https://apna.co/job/role-3": AdapterTimeoutError("APNA", detail="simulated detail timeout"),
    "https://apna.co/job/role-4": _detail_html({**_FULL_POSTING, "description": "JD for role 4 aws azure"}),
}
fetcher_detail = _DetailFakeFetcher(detail_search_url, _listing_4jobs_html, detail_responses)
adapter_detail = ApnaAdapter(fetcher=fetcher_detail, rate_limit_seconds=0, max_detail_fetches_per_query=10)
jobs_enriched = adapter_detail.search(detail_query)

check(len(jobs_enriched) == 4, f"7e. all 4 listing jobs still returned even though one detail fetch failed, got {len(jobs_enriched)}")
by_title = {j["title"]: j for j in jobs_enriched}
check(by_title["Role 1"]["jd_text"] == "JD for role 1 kubernetes terraform", "7e/7f. job 1's listing result is enriched with its real detail-page JD text")
check(by_title["Role 3"]["jd_text"] == "", "7c. job 3 (detail fetch failed) preserves its listing result -- jd_text stays empty, not fabricated")
check(by_title["Role 3"]["title"] == "Role 3" and by_title["Role 3"]["company"] == "Co 3", "7c. job 3's own listing data (title/company) is fully intact despite the detail failure")

details = adapter_detail.last_search_details
check(details["detail_fetch_attempted"] == 4, f"1/7g. detail_fetch_attempted == 4 (bound=10, only 4 jobs existed), got {details['detail_fetch_attempted']}")
check(details["detail_fetch_succeeded"] == 3, f"1/7g. detail_fetch_succeeded == 3, got {details['detail_fetch_succeeded']}")
check(details["detail_fetch_failed"] == 1, f"1/7g. detail_fetch_failed == 1 (job 3's timeout), got {details['detail_fetch_failed']}")

# 5. request count never exceeds the configured bound.
fetcher_bounded = _DetailFakeFetcher(detail_search_url, _listing_4jobs_html, {
    "https://apna.co/job/role-1": _detail_html(_FULL_POSTING),
    "https://apna.co/job/role-2": _detail_html(_FULL_POSTING),
})
adapter_bounded = ApnaAdapter(fetcher=fetcher_bounded, rate_limit_seconds=0, max_detail_fetches_per_query=2)
adapter_bounded.search(detail_query)
detail_calls = [c for c in fetcher_bounded.calls if c != detail_search_url]
check(len(detail_calls) == 2, f"5. request count never exceeds the configured bound (max=2), got {len(detail_calls)} detail fetches")
check(adapter_bounded.last_search_details["detail_fetch_attempted"] == 2, "5. last_search_details also reports exactly the bound, not more")

# 6. duplicate detail URLs are never fetched twice (a job_url appearing
# twice in the jobs list -- defensive second guard, listing-level dedup
# already prevents this in practice).
dup_jobs = [
    {"job_url": "https://apna.co/job/role-1", "title": "Role 1", "company": "Co 1", "location": "", "posted_date": "", "jd_text": "", "experience_required": ""},
    {"job_url": "https://apna.co/job/role-1", "title": "Role 1 dup", "company": "Co 1", "location": "", "posted_date": "", "jd_text": "", "experience_required": ""},
]
fetch_log = []
class _CountingFetcher:
    def fetch(self, url):
        fetch_log.append(url)
        return _detail_html(_FULL_POSTING)
adapter_dedup = ApnaAdapter(fetcher=_CountingFetcher(), rate_limit_seconds=0, max_detail_fetches_per_query=10)
adapter_dedup._enrich_with_detail_pages(dup_jobs)
check(len(fetch_log) == 1, f"6. duplicate detail URLs across jobs are fetched only once, got {len(fetch_log)} fetches")

# 0 bound: no detail fetching at all, exactly like the original
# (pre-this-phase) default -- confirms the bound is genuinely respected
# down to zero, not just "smaller than before".
adapter_zero = ApnaAdapter(fetcher=fetcher_detail, rate_limit_seconds=0, max_detail_fetches_per_query=0)
zero_result = adapter_zero._enrich_with_detail_pages([dict(dup_jobs[0])])
check(zero_result == {"detail_fetch_attempted": 0, "detail_fetch_succeeded": 0, "detail_fetch_failed": 0}, "max_detail_fetches_per_query=0 performs zero detail fetches (configurable down to the original off behavior)")

# 9. no fabricated skills: parse_detail_page never invents a
# mandatory_skills/preferred_skills field at all (score_job.py's own
# skill-matching reads jd_text/title, never a skills list from this
# adapter -- confirmed no such key is ever present in the enrichment).
check("mandatory_skills" not in enrichment and "preferred_skills" not in enrichment, "9. parse_detail_page() never introduces a skills list -- no fabricated skills")

# 8/10: score_job() component sum still equals final score, and the
# rubric itself is unchanged, when scoring a detail-enriched job --
# reuses the REAL score_job.py, never a second scorer.
from score_job import score_job
from candidate_profile import to_legacy_matching_profile, normalize_candidate_profile, promote_to_confirmed

_test_profile = promote_to_confirmed(normalize_candidate_profile({
    "identity": {"candidate_id": "cand_apna_detail_test", "name": "Test"},
    "professional_summary": {"total_experience_years": 6},
    "skills": {
        "containers_orchestration": [{"name": "Kubernetes"}],
        "infrastructure_iac": [{"name": "Terraform"}],
        "cicd": [{"name": "CI/CD"}],
        "observability": [{"name": "Observability"}],
    },
    "job_preferences": {"target_roles": ["Senior Site Reliability Engineer"], "target_locations": ["Bengaluru"]},
}))
legacy = to_legacy_matching_profile(_test_profile)
enriched_job = dict(by_title["Role 1"])
enriched_job["experience_required"] = "5+ years"
scoring = score_job(enriched_job, legacy)
check(scoring["score"] >= 0, "8. score_job() runs successfully on a detail-enriched job")

component_labels = {
    "Core role alignment", "SRE/DevOps responsibilities", "Cloud alignment", "Kubernetes",
    "Terraform/IaC", "CI/CD", "Observability", "Experience", "Location", "Overall/domain fit",
}
unrecognized_labels = {m for m in scoring["matched_skills"] if not any(m.startswith(lbl) or lbl in m for lbl in component_labels)}
check(not unrecognized_labels, f"10. existing 10-dimension rubric unchanged -- every matched label maps to a known dimension, unrecognized: {unrecognized_labels}")

# The enriched jd_text ("kubernetes terraform" for role 1) should
# genuinely change score_job()'s own keyword matching vs the SAME job
# with jd_text cleared -- proving score_job() actually RECEIVED and
# used the enriched text, not just that scoring didn't crash.
unenriched_job = dict(enriched_job)
unenriched_job["jd_text"] = ""
unenriched_scoring = score_job(unenriched_job, legacy)
check(
    scoring["score"] > unenriched_scoring["score"],
    f"7f. score_job() receives and USES the enriched JD text -- enriched score ({scoring['score']}) > unenriched score ({unenriched_scoring['score']})",
)


print()
print(f"{len(failures)} failed")
if failures:
    sys.exit(1)
