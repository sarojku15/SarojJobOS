#!/usr/bin/env python3

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from source_adapter import (
    SearchQuery,
    BlockReason,
    AdapterBlockedError,
    AdapterTimeoutError,
)
from naukri_parser import parse_search_results
from naukri_adapter import NaukriAdapter

FIXTURES_DIR = ROOT / "data" / "fixtures" / "naukri"

EXPECTED_TCS_URL = (
    "https://www.naukri.com/job-listings-site-reliability-engineer-"
    "tata-consultancy-services-bengaluru-8-to-10-years-190626026419"
)
EXPECTED_TCS_TITLE = "Site Reliability Engineer"
EXPECTED_TCS_COMPANY = "Tata Consultancy Services"
EXPECTED_TCS_LOCATION_SUBSTRING = "Bengaluru"

QUERY = SearchQuery(
    role="Senior Site Reliability Engineer",
    location="Bengaluru",
)


def load_fixture(filename):
    return (FIXTURES_DIR / filename).read_text(encoding="utf-8")


class FakeFetcher:
    """
    Serves a pre-configured sequence of responses, one per call, in
    call order -- regardless of the URL requested. Every requested URL
    is recorded in `requested_urls` so tests can verify fetch order
    and count (e.g. confirming no fetches happened after a block).

    This makes tests independent of NaukriAdapter's exact URL-building
    logic (health-check URL, search-URL slugification): what matters
    for these tests is WHICH fixture content is served at each step of
    the health_check()/search() flow, not the literal URL string.

    An entry in `responses` that is an Exception instance is raised
    instead of returned, so detail_page_timeout can simulate
    AdapterTimeoutError on a specific call.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.requested_urls = []

    def fetch(self, url):
        self.requested_urls.append(url)

        if not self._responses:
            raise AssertionError(
                "FakeFetcher received more fetch() calls than "
                f"configured responses (url={url!r})"
            )

        response = self._responses.pop(0)

        if isinstance(response, Exception):
            raise response

        return response


def main():
    failures = []

    homepage_html = load_fixture("homepage.html")
    search_html = load_fixture("search_results_sre_bengaluru.html")
    detail_html = load_fixture("detail_valid.html")
    blocked_html = load_fixture("blocked_access_denied.html")

    candidate_count = len(parse_search_results(search_html))

    print("NAUKRI ADAPTER TEST")
    print("====================")
    print(f"Real search fixture candidate count: {candidate_count}")
    print()

    # --- 1. health_check_success ---
    print("Case 1: health_check_success")
    fetcher = FakeFetcher([homepage_html])
    adapter = NaukriAdapter(fetcher=fetcher, rate_limit_seconds=0)
    health = adapter.health_check()

    if health.reachable is not True:
        failures.append(
            f"health_check_success: expected reachable=True, "
            f"got {health.reachable}"
        )
    elif health.block_reason != BlockReason.NONE:
        failures.append(
            "health_check_success: expected BlockReason.NONE, "
            f"got {health.block_reason}"
        )
    else:
        print(
            f"  PASS: reachable={health.reachable}, "
            f"block_reason={health.block_reason}"
        )

    # --- 2. health_check_blocked ---
    print("Case 2: health_check_blocked")
    fetcher = FakeFetcher([blocked_html])
    adapter = NaukriAdapter(fetcher=fetcher, rate_limit_seconds=0)
    health = adapter.health_check()

    if health.reachable is not False:
        failures.append(
            f"health_check_blocked: expected reachable=False, "
            f"got {health.reachable}"
        )
    elif health.block_reason != BlockReason.UNKNOWN_BLOCK:
        failures.append(
            "health_check_blocked: expected BlockReason.UNKNOWN_BLOCK, "
            f"got {health.block_reason}"
        )
    else:
        print(
            f"  PASS: reachable={health.reachable}, "
            f"block_reason={health.block_reason}"
        )

    # --- 3. search_success ---
    print("Case 3: search_success")
    fetcher = FakeFetcher([search_html] + [detail_html] * candidate_count)
    adapter = NaukriAdapter(fetcher=fetcher, rate_limit_seconds=0)
    jobs = adapter.search(QUERY)

    if not jobs:
        failures.append(
            "search_success: expected a non-empty list of jobs, got empty"
        )
    else:
        tcs_matches = [
            job for job in jobs if job.get("job_url") == EXPECTED_TCS_URL
        ]

        if not tcs_matches:
            failures.append(
                f"search_success: expected job_url {EXPECTED_TCS_URL} "
                "in results"
            )
        else:
            entry = tcs_matches[0]

            if entry.get("title") != EXPECTED_TCS_TITLE:
                failures.append(
                    "search_success: title mismatch, "
                    f"got {entry.get('title')!r}"
                )
            elif entry.get("company") != EXPECTED_TCS_COMPANY:
                failures.append(
                    "search_success: company mismatch, "
                    f"got {entry.get('company')!r}"
                )
            elif EXPECTED_TCS_LOCATION_SUBSTRING not in entry.get(
                "location", ""
            ):
                failures.append(
                    "search_success: location mismatch, "
                    f"got {entry.get('location')!r}"
                )
            else:
                print(
                    f"  PASS: {len(jobs)} jobs returned, "
                    "TCS entry verified"
                )

    # --- 4. search_page_blocked ---
    print("Case 4: search_page_blocked")
    fetcher = FakeFetcher([blocked_html])
    adapter = NaukriAdapter(fetcher=fetcher, rate_limit_seconds=0)

    try:
        adapter.search(QUERY)
        failures.append(
            "search_page_blocked: expected AdapterBlockedError, "
            "none was raised"
        )
    except AdapterBlockedError:
        if len(fetcher.requested_urls) != 1:
            failures.append(
                "search_page_blocked: expected exactly 1 fetch "
                f"(search page only), got {len(fetcher.requested_urls)}"
            )
        else:
            print(
                "  PASS: AdapterBlockedError raised, "
                f"{len(fetcher.requested_urls)} fetch(es) made"
            )

    # --- 5. detail_page_blocked ---
    print("Case 5: detail_page_blocked")
    fetcher = FakeFetcher([search_html, detail_html, blocked_html])
    adapter = NaukriAdapter(fetcher=fetcher, rate_limit_seconds=0)
    jobs = adapter.search(QUERY)

    if len(jobs) != 1:
        failures.append(
            f"detail_page_blocked: expected exactly 1 job returned, "
            f"got {len(jobs)}"
        )
    elif len(fetcher.requested_urls) != 3:
        failures.append(
            "detail_page_blocked: expected exactly 3 fetches "
            f"(search + 2 details), got {len(fetcher.requested_urls)}"
        )
    else:
        print(
            f"  PASS: {len(jobs)} job(s) returned, fetching stopped "
            f"after the block ({len(fetcher.requested_urls)} fetches)"
        )

    # --- 6. detail_page_timeout ---
    print("Case 6: detail_page_timeout")
    responses = [
        search_html,
        AdapterTimeoutError("NAUKRI", detail="simulated timeout"),
    ]
    responses += [detail_html] * (candidate_count - 1)

    fetcher = FakeFetcher(responses)
    adapter = NaukriAdapter(fetcher=fetcher, rate_limit_seconds=0)
    jobs = adapter.search(QUERY)

    expected_job_count = candidate_count - 1
    expected_fetch_count = 1 + candidate_count

    if len(jobs) != expected_job_count:
        failures.append(
            f"detail_page_timeout: expected {expected_job_count} jobs "
            f"(one skipped), got {len(jobs)}"
        )
    elif len(fetcher.requested_urls) != expected_fetch_count:
        failures.append(
            "detail_page_timeout: expected the search fetch plus one "
            f"attempt per candidate ({expected_fetch_count} total), "
            f"got {len(fetcher.requested_urls)}"
        )
    else:
        print(
            f"  PASS: {len(jobs)} job(s) returned (1 timed-out detail "
            "skipped), remaining candidates still fetched"
        )

    # --- 7. search_valid_empty_result ---
    print("Case 7: search_valid_empty_result")
    empty_html = load_fixture("search_results_empty_valid.html")
    fetcher = FakeFetcher([empty_html])
    adapter = NaukriAdapter(fetcher=fetcher, rate_limit_seconds=0)

    try:
        jobs = adapter.search(QUERY)
        if jobs != []:
            failures.append(
                f"search_valid_empty_result: expected [], got {jobs!r}"
            )
        elif len(fetcher.requested_urls) != 1:
            failures.append(
                "search_valid_empty_result: expected exactly 1 fetch "
                f"(search page only), got {len(fetcher.requested_urls)}"
            )
        else:
            print(
                "  PASS: search() returned [] with no exception, "
                f"{len(fetcher.requested_urls)} fetch(es) made"
            )
    except Exception as error:
        failures.append(
            f"search_valid_empty_result: expected no exception, got {error!r}"
        )

    # --- 8. search_soft_block_or_challenge ---
    print("Case 8: search_soft_block_or_challenge")
    soft_block_html = load_fixture("search_results_soft_block.html")
    fetcher = FakeFetcher([soft_block_html])
    adapter = NaukriAdapter(fetcher=fetcher, rate_limit_seconds=0)

    try:
        adapter.search(QUERY)
        failures.append(
            "search_soft_block_or_challenge: expected AdapterBlockedError, "
            "none was raised"
        )
    except AdapterBlockedError as error:
        if len(fetcher.requested_urls) != 1:
            failures.append(
                "search_soft_block_or_challenge: expected exactly 1 fetch "
                f"(search page only), got {len(fetcher.requested_urls)}"
            )
        else:
            print(
                f"  PASS: AdapterBlockedError raised ({error}), "
                f"{len(fetcher.requested_urls)} fetch(es) made"
            )
    except Exception as error:
        failures.append(
            "search_soft_block_or_challenge: expected AdapterBlockedError, "
            f"got {type(error).__name__}: {error}"
        )

    # --- 9. search_parse_failure ---
    print("Case 9: search_parse_failure")
    unrecognized_html = load_fixture("unrecognized_structure.html")
    fetcher = FakeFetcher([unrecognized_html])
    adapter = NaukriAdapter(fetcher=fetcher, rate_limit_seconds=0)

    try:
        adapter.search(QUERY)
        failures.append(
            "search_parse_failure: expected AdapterTimeoutError, "
            "none was raised"
        )
    except AdapterTimeoutError as error:
        if len(fetcher.requested_urls) != 1:
            failures.append(
                "search_parse_failure: expected exactly 1 fetch "
                f"(search page only), got {len(fetcher.requested_urls)}"
            )
        else:
            print(
                f"  PASS: AdapterTimeoutError raised ({error}), "
                f"{len(fetcher.requested_urls)} fetch(es) made -- "
                "not recorded as a successful zero-result query"
            )
    except Exception as error:
        failures.append(
            "search_parse_failure: expected AdapterTimeoutError, "
            f"got {type(error).__name__}: {error}"
        )

    # --- 10. real NaukriFetcher subprocess exception handling
    # (2026-09-28 hardening pass) -- subprocess.run() itself (not the
    # bridge script) can fail before ever producing a CompletedProcess.
    # Only subprocess.TimeoutExpired was previously caught; FileNotFound/
    # OSError/UnicodeDecodeError escaped uncaught. Uses the REAL
    # NaukriFetcher (not FakeFetcher) with subprocess.run patched.
    import subprocess as _subprocess
    from unittest.mock import patch as _patch
    from naukri_fetcher import NaukriFetcher

    class _FakeCompletedProcess:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    print("Case 10a: fetcher_subprocess_file_not_found")
    with _patch.object(_subprocess, "run", side_effect=FileNotFoundError("node: command not found")):
        try:
            NaukriFetcher().fetch("https://www.naukri.com/")
            failures.append("fetcher_subprocess_file_not_found: expected AdapterTimeoutError, none raised")
        except AdapterTimeoutError:
            print("  PASS: FileNotFoundError (missing `node`) converted to AdapterTimeoutError")
        except Exception as error:
            failures.append(f"fetcher_subprocess_file_not_found: expected AdapterTimeoutError, got {type(error).__name__}: {error}")

    print("Case 10b: fetcher_subprocess_unicode_decode_error")
    with _patch.object(_subprocess, "run", side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")):
        try:
            NaukriFetcher().fetch("https://www.naukri.com/")
            failures.append("fetcher_subprocess_unicode_decode_error: expected AdapterTimeoutError, none raised")
        except AdapterTimeoutError:
            print("  PASS: UnicodeDecodeError decoding subprocess output converted to AdapterTimeoutError")
        except Exception as error:
            failures.append(f"fetcher_subprocess_unicode_decode_error: expected AdapterTimeoutError, got {type(error).__name__}: {error}")

    print("Case 10c: fetcher_subprocess_timeout_still_works")
    with _patch.object(_subprocess, "run", side_effect=_subprocess.TimeoutExpired(cmd=["node"], timeout=75)):
        try:
            NaukriFetcher().fetch("https://www.naukri.com/")
            failures.append("fetcher_subprocess_timeout_still_works: expected AdapterTimeoutError, none raised")
        except AdapterTimeoutError:
            print("  PASS: subprocess.TimeoutExpired still converts to AdapterTimeoutError exactly as before")
        except Exception as error:
            failures.append(f"fetcher_subprocess_timeout_still_works: expected AdapterTimeoutError, got {type(error).__name__}: {error}")

    print("Case 10d: fetcher_subprocess_nonzero_returncode_still_works")
    with _patch.object(_subprocess, "run", return_value=_FakeCompletedProcess(1, "", "bridge crashed")):
        try:
            NaukriFetcher().fetch("https://www.naukri.com/")
            failures.append("fetcher_subprocess_nonzero_returncode_still_works: expected AdapterTimeoutError, none raised")
        except AdapterTimeoutError as error:
            if "bridge crashed" in str(error):
                print("  PASS: non-zero return code still converts to AdapterTimeoutError with stderr detail, unaffected by this fix")
            else:
                failures.append(f"fetcher_subprocess_nonzero_returncode_still_works: AdapterTimeoutError raised but detail lost stderr: {error}")
        except Exception as error:
            failures.append(f"fetcher_subprocess_nonzero_returncode_still_works: expected AdapterTimeoutError, got {type(error).__name__}: {error}")

    print("Case 10e: fetcher_subprocess_success_still_works")
    with _patch.object(_subprocess, "run", return_value=_FakeCompletedProcess(0, "<html>ok</html>", "")):
        try:
            body = NaukriFetcher().fetch("https://www.naukri.com/")
            if body == "<html>ok</html>":
                print("  PASS: a successful subprocess run still returns stdout unaffected by this fix")
            else:
                failures.append(f"fetcher_subprocess_success_still_works: unexpected body {body!r}")
        except Exception as error:
            failures.append(f"fetcher_subprocess_success_still_works: expected success, got {type(error).__name__}: {error}")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll Naukri adapter tests passed.")


if __name__ == "__main__":
    main()
