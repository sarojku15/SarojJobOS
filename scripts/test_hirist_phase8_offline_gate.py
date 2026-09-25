#!/usr/bin/env python3

"""
Phase 8 PART D -- Hirist OFFLINE GATE (data/reports/phase8_multisource_validation.md).

Verifies every previous Hirist finding against the CURRENT, freshly-
read code (not assumed from memory) and against real captured data,
using both pre-existing fixtures (data/fixtures/hirist/*.html, Phase 6)
and three new ones added this phase specifically for Phase 8's
required coverage (missing_url.html, duplicate_url.html,
company_title_ambiguity_real_capture.html -- the last one is the
VERBATIM real JSON-LD from Phase 6 Step 9's one live capture, not
synthetic).

Makes ZERO network requests. This file's own conclusion feeds directly
into whether Hirist proceeds to PART D's live gate at all.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "data" / "fixtures" / "hirist"
sys.path.insert(0, str(ROOT / "scripts"))

from hirist_parser import classify_search_page, HiristPageState, _parse_job_entry
from hirist_adapter import HiristAdapter
from source_adapter import AdapterStatus, AdapterCapability


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _read_fixture(name):
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def main():
    failures = []
    print("HIRIST PHASE 8 OFFLINE GATE")
    print("=================================================================================")

    # --- 1. Re-verify previous findings against CURRENT code (fresh, not assumed) ---
    checks = [
        ("valid_results_page1.html", HiristPageState.VALID_RESULTS, "JSON-LD extraction + pagination link discovery"),
        ("valid_results_page2.html", HiristPageState.VALID_RESULTS, "last-page pagination (no next link)"),
        ("empty_results.html", HiristPageState.VALID_EMPTY_RESULT, "genuine empty result, distinguishable from parse failure"),
        ("blocked_access_denied.html", HiristPageState.BLOCKED, "hard-block detection"),
        ("challenge_captcha.html", HiristPageState.SOFT_BLOCK_OR_CHALLENGE, "soft-challenge / visible-text detection"),
        ("malformed_json_ld.html", HiristPageState.PARSE_FAILURE, "malformed JSON-LD handling"),
        ("missing_json_ld.html", HiristPageState.PARSE_FAILURE, "missing JSON-LD handling"),
    ]
    for fixture, expected_state, label in checks:
        classification = classify_search_page(_read_fixture(fixture), "https://www.hirist.tech/search/x")
        if classification.state != expected_state:
            _fail(failures, f"1. {label} ({fixture}): expected {expected_state}, got {classification.state}")
    if not any(f.startswith("1.") for f in failures):
        print(f"PASS: 1. all {len(checks)} previously-established classifier findings re-verified against the CURRENT code, unchanged")

    # --- 2. Company extraction / title extraction / location / experience /
    # description / deterministic job ID / canonical URL -- re-verify via
    # the REAL captured data (not synthetic) ---
    classification = classify_search_page(
        _read_fixture("company_title_ambiguity_real_capture.html"),
        "https://www.hirist.tech/search/senior-devops-engineer-jobs?locations=Bengaluru",
    )
    if classification.state != HiristPageState.VALID_RESULTS:
        _fail(failures, f"2. real-capture fixture: expected VALID_RESULTS, got {classification.state}")
    elif len(classification.jobs) != 10:
        _fail(failures, f"2. real-capture fixture: expected 10 successfully-parsed jobs (matching Step 9's live result exactly), got {len(classification.jobs)}")
    else:
        print(f"PASS: 2. real-capture fixture re-parses identically to Step 9's live result: 10/20 entries successfully parsed")

    job_urls = {j["job_url"] for j in classification.jobs}
    if "" in job_urls or len(job_urls) != len(classification.jobs):
        _fail(failures, f"2b. job_url extraction: expected every parsed job to have a unique, non-empty job_url, got {job_urls}")
    else:
        print(f"PASS: 2b. job_url extraction confirmed working on real data -- {len(job_urls)} unique, non-empty URLs (also serves as the deterministic job-ID basis via job_id.job_id_from_url())")

    # --- 3. THE decisive check: company/title ambiguity, with exact real examples ---
    wrong_split_examples = [j for j in classification.jobs if j["company"] in ("Senior DevOps Engineer",)]
    if len(wrong_split_examples) < 1:
        _fail(failures, "3. expected at least one confirmed-wrong company/title split from the real data, found none -- this would mean the known limitation was somehow already fixed; investigate before concluding anything")
    else:
        print(f"PASS: 3. company/title ambiguity CONFIRMED, again, on real data -- {len(wrong_split_examples)} entries wrongly split: " +
              ", ".join(f"company={j['company']!r} title={j['title']!r}" for j in wrong_split_examples))

    skipped_count = 20 - len(classification.jobs)
    print(f"       Additionally: {skipped_count}/20 real entries were SKIPPED entirely (fails closed, multi-delimiter 'Company - Title - Tags' convention) -- "
          f"a second, distinct manifestation of the same underlying ambiguity (entries silently lost, not merely mislabeled).")

    # --- 4. Location / experience / description -- confirmed absent from real data (unchanged) ---
    locations = {j["location"] for j in classification.jobs if j["location"]}
    experiences = {j["experience_required"] for j in classification.jobs if j["experience_required"]}
    descriptions = {j["jd_text"] for j in classification.jobs if j["jd_text"]}
    if locations or experiences or descriptions:
        _fail(failures, f"4. expected location/experience/description to remain empty on all real-data entries (nested-item path never exercised), got locations={locations} experiences={experiences} descriptions={bool(descriptions)}")
    else:
        print("PASS: 4. location/experience/description confirmed still absent from real Hirist search-results data (nested-item schema.org path remains unexercised) -- unchanged from Step 9")

    # --- 5. Missing URL: parser must never construct/guess one ---
    classification_missing = classify_search_page(_read_fixture("missing_url.html"), "https://www.hirist.tech/search/x")
    if classification_missing.state != HiristPageState.VALID_RESULTS:
        _fail(failures, f"5. missing_url.html: expected VALID_RESULTS, got {classification_missing.state}")
    elif len(classification_missing.jobs) != 1 or classification_missing.jobs[0]["job_url"] != "":
        _fail(failures, f"5. missing_url.html: expected 1 job with job_url=='', got {classification_missing.jobs}")
    else:
        print("PASS: 5. an entry with no url field parses successfully with job_url left empty -- never guessed/constructed")

    # --- 6. Duplicate URL: both entries parse independently, neither corrupted ---
    classification_dup = classify_search_page(_read_fixture("duplicate_url.html"), "https://www.hirist.tech/search/x")
    if classification_dup.state != HiristPageState.VALID_RESULTS:
        _fail(failures, f"6. duplicate_url.html: expected VALID_RESULTS, got {classification_dup.state}")
    elif len(classification_dup.jobs) != 2:
        _fail(failures, f"6. duplicate_url.html: expected 2 independently-parsed jobs, got {len(classification_dup.jobs)}")
    else:
        urls = [j["job_url"] for j in classification_dup.jobs]
        titles = [j["title"] for j in classification_dup.jobs]
        if urls[0] != urls[1]:
            _fail(failures, f"6. duplicate_url.html: expected both entries to share the identical url, got {urls}")
        elif titles[0] == titles[1]:
            _fail(failures, f"6. duplicate_url.html: expected each entry's OWN distinct title to be preserved (not merged/corrupted), got {titles}")
        else:
            print(f"PASS: 6. duplicate URL entries parse independently, each keeping its own company/title -- URL-level deduplication is correctly left to discover_local.deduplicate() downstream, not this module")

    # --- 7. Pagination -- unchanged, re-verified ---
    next_url = classification.next_url if classification.next_url else None
    classification_p1 = classify_search_page(_read_fixture("valid_results_page1.html"), "https://www.hirist.tech/search/senior-devops-engineer-jobs")
    if classification_p1.next_url != "https://www.hirist.tech/search/senior-devops-engineer-jobs?page=2":
        _fail(failures, f"7. pagination: expected the known next_url, got {classification_p1.next_url}")
    else:
        print("PASS: 7. pagination link discovery unchanged, re-verified")

    # --- 8. HiristAdapter enablement state ---
    # Phase 11 superseded this file's own Phase 8 decision: the flat
    # listing-page "name" string's ambiguity documented below (checks
    # 1-7, still an accurate, unchanged description of that string's
    # own limitations) is no longer the basis for company/title
    # extraction at all -- HiristAdapter.search() now resolves company
    # via each job's own DETAIL page (schema.org JobPosting JSON-LD,
    # hiringOrganization.name), validated 10/10 correct against real
    # data (data/reports/phase11_public_multisource_completion.md).
    # HiristAdapter is therefore now genuinely ENABLED.
    expected_caps = {AdapterCapability.SEARCH, AdapterCapability.DETAIL, AdapterCapability.PAGINATION}
    if HiristAdapter.status != AdapterStatus.ENABLED:
        _fail(failures, f"8. HiristAdapter.status is {HiristAdapter.status}, expected ENABLED (Phase 11 superseded this file's Phase 8 decision)")
    elif HiristAdapter.capabilities != expected_caps:
        _fail(failures, f"8. HiristAdapter.capabilities = {HiristAdapter.capabilities}, expected {expected_caps}")
    else:
        print("PASS: 8. HiristAdapter is genuinely ENABLED (Phase 11) -- the flat-name ambiguity below is no longer load-bearing")

    # === DECISION ===
    print()
    print("=================================================================================")
    print("HIRIST OFFLINE GATE DECISION (Phase 8 finding; see Phase 11 supersession note above)")
    print("=================================================================================")
    if failures:
        print("BLOCKED -- offline checks themselves failed; see FAILURES above. Cannot proceed to any live gate.")
    else:
        print("The LISTING PAGE's flat 'name' string remains MATERIALLY UNRELIABLE on its own (Phase 8 finding,")
        print("still true, still why _parse_job_entry()'s old flat-name path is never used for company")
        print("extraction any more):")
        print("  - at least 1/10 successfully-parsed real entries has a CONFIRMED WRONG company/title split")
        print("    (company='Senior DevOps Engineer', title='AWS & Kubernetes' -- this is a job TITLE being")
        print("    split in half and the first half mislabeled as a company name)")
        print("  - 10/20 real entries are silently SKIPPED entirely (a second, distinct data-loss manifestation)")
        print("  - the root cause is structural: real 'name' values mix at least 3 conventions")
        print("    (bare title / 'Company - Title' / 'Company - Title - Tags') with no way to")
        print("    distinguish them from the string alone -- confirmed by direct inspection of the")
        print("    real captured JSON-LD, not inferred")
        print()
        print("PHASE 11 RESOLUTION: rather than fixing the listing-page string (impossible without")
        print("guessing), HiristAdapter.search() now fetches each job's own DETAIL page and reads")
        print("schema.org JobPosting.hiringOrganization.name instead -- validated 10/10 correct against")
        print("real data. DECISION: HIRIST IS NOW ENABLED (see data/reports/phase11_public_multisource_completion.md).")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)

    print("\nAll Phase 8 Hirist offline-gate checks completed (decision superseded by Phase 11: ENABLED, documented above).")


if __name__ == "__main__":
    main()
