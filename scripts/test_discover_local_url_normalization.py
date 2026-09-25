#!/usr/bin/env python3

"""
Regression test for a confirmed bug found during the live-Naukri
score-quality audit (data/reports/live_naukri_score_audit.md):
discover_local.normalize_url()'s URL-extraction regex was written with
a literal double backslash inside a raw string (four backslash
characters followed by "s)]+"), which the regex engine reads as
"exclude a literal backslash, the letter s, or a closing paren" --
NOT "exclude whitespace" as intended (a single backslash before s).

Effect: any URL containing a lowercase 's' in its path before any
whitespace/closing-paren was silently truncated right before that 's'.
Naukri's own URL pattern always contains "job-listings" immediately
after the domain, so every single Naukri job_url/application_url
normalized through this function was truncated to
"https://www.naukri.com/job-li" -- confirmed against the real captured
temp DB from the live validation run (all 20 rows, byte-for-byte
identical 29-character truncated value).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from discover_local import normalize_url, normalize_job

REAL_NAUKRI_URL = (
    "https://www.naukri.com/job-listings-site-reliability-engineer-"
    "tata-consultancy-services-bengaluru-8-to-10-years-190626026419"
)


def main():
    failures = []

    print("DISCOVER_LOCAL URL NORMALIZATION TEST")
    print("=======================================")

    # --- the exact regression: a real captured Naukri URL must survive
    #     normalize_url() completely intact ---
    result = normalize_url(REAL_NAUKRI_URL)
    if result != REAL_NAUKRI_URL:
        failures.append(
            f"REGRESSION: normalize_url() truncated a real Naukri URL. "
            f"Expected {REAL_NAUKRI_URL!r} ({len(REAL_NAUKRI_URL)} chars), "
            f"got {result!r} ({len(result)} chars)"
        )
    else:
        print(f"PASS: real captured Naukri URL survives normalize_url() intact ({len(result)} chars)")

    # --- any URL with a lowercase 's' in the path, not just Naukri's ---
    for url in [
        "https://example.com/jobs/senior-sre",
        "https://boards.example.com/postings/12345",
        "https://example.com/careers/site-reliability-engineer",
    ]:
        result = normalize_url(url)
        if result != url:
            failures.append(f"REGRESSION: normalize_url({url!r}) returned {result!r} (truncated)")
        else:
            print(f"PASS: normalize_url({url!r}) -> unchanged")

    # --- normalize_job() end-to-end: job_url/application_url survive ---
    raw_job = {
        "source": "NAUKRI",
        "company": "Tata Consultancy Services",
        "title": "Site Reliability Engineer",
        "location": "Bengaluru",
        "job_url": REAL_NAUKRI_URL,
        "application_url": REAL_NAUKRI_URL,
    }
    normalized = normalize_job(raw_job, 1)
    if normalized["job_url"] != REAL_NAUKRI_URL or normalized["application_url"] != REAL_NAUKRI_URL:
        failures.append(
            f"REGRESSION: normalize_job() truncated job_url/application_url: "
            f"job_url={normalized['job_url']!r}, application_url={normalized['application_url']!r}"
        )
    else:
        print("PASS: normalize_job() preserves the full job_url and application_url")

    # --- still correctly extracts a bare URL embedded in surrounding
    #     text, and still strips trailing markdown/whitespace noise
    #     (the original, still-intended behavior of this function) ---
    if normalize_url("  https://example.com/jobs/sre  some trailing words") != "https://example.com/jobs/sre":
        failures.append("normalize_url() no longer extracts a bare URL from surrounding text correctly")
    else:
        print("PASS: normalize_url() still extracts a URL embedded in surrounding text")

    if normalize_url("[Apply here](https://example.com/jobs/senior-sre)") != "https://example.com/jobs/senior-sre":
        failures.append("normalize_url() no longer handles markdown-style links correctly")
    else:
        print("PASS: normalize_url() still handles markdown-style links correctly")

    if normalize_url("") != "":
        failures.append("normalize_url('') should return ''")
    else:
        print("PASS: normalize_url('') -> ''")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll discover_local URL normalization tests passed.")


if __name__ == "__main__":
    main()
