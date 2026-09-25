#!/usr/bin/env python3

"""
iimjobs search/detail page parsing (Phase 11). iimjobs shares the EXACT
same underlying platform as Hirist -- confirmed via a controlled live
inspection this phase (data/reports/phase11_public_multisource_completion.md):
byte-identical robots.txt (same Joomla-style admin paths, same
Crawl-delay: 10, same Yandex block), the same schema.org ItemList
(flat "name"/"url" ListItem entries) on search-results pages, the same
schema.org JobPosting (hiringOrganization.name) on detail pages, and
even the same S3 asset bucket for company logos
(recruiter-static-content.s3.ap-south-1.amazonaws.com).

Rather than duplicate hirist_parser.py's generic schema.org parsing
logic (none of which is actually Hirist-specific), this module reuses
it directly: _find_item_list_json_ld, _parse_job_entry_title_only,
parse_job_detail_json_ld, _find_block_signal, and classify_search_page
(with iimjobs' own host passed via allowed_next_host). Only the
site-specific constant (this module's own _ALLOWED_NEXT_URL_HOST) is
new.
"""

from hirist_parser import (  # noqa: F401 -- re-exported for iimjobs_adapter.py
    HiristPageState as IimjobsPageState,
    HiristSearchClassification as IimjobsSearchClassification,
    _find_block_signal,
    _find_item_list_json_ld,
    _parse_job_entry_title_only,
    classify_search_page as _classify_search_page_generic,
    parse_job_detail_json_ld,
)

_ALLOWED_NEXT_URL_HOST = "www.iimjobs.com"


def classify_search_page(html, current_url):
    """Thin wrapper: hirist_parser.classify_search_page() with
    iimjobs' own host and the title-only entry parser (this module's
    two-stage listing+detail-page pipeline never uses the flat-name
    company/title split at all -- see iimjobs_adapter.py)."""
    return _classify_search_page_generic(
        html,
        current_url,
        entry_parser=_parse_job_entry_title_only,
        allowed_next_host=_ALLOWED_NEXT_URL_HOST,
    )
