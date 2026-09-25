#!/usr/bin/env python3

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from html.parser import HTMLParser

from source_adapter import BlockReason


class _VisibleTextExtractor(HTMLParser):
    """
    Extracts only human-visible text from an HTML document: the
    <title> and body text nodes, explicitly excluding the contents of
    <script> and <style> elements along with all tag names,
    attributes, and CSS class names.

    This distinction is what makes detect_block_reason() safe against
    false positives. Naive tag-stripping (e.g. a regex that just
    removes <...> spans) leaves CSS rule text and class-name strings
    behind as if they were visible page content. On
    search_results_sre_bengaluru.html, that naive approach "sees" the
    words "captcha" and "verify" inside a dormant .bot-guard-captcha
    CSS rule and an otpMobileVerifyContainer class name -- neither of
    which a human visitor ever sees rendered on the page. Skipping
    <script>/<style> element content entirely removes this whole class
    of false positive structurally, rather than trying to out-guess
    every noisy keyword that might appear in markup.
    """

    _SKIP_TAGS = {"script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._title_parts = []
        self._body_parts = []
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
        else:
            self._body_parts.append(data)

    @property
    def title(self):
        return "".join(self._title_parts).strip()

    @property
    def visible_text(self):
        return " ".join(
            part.strip() for part in self._body_parts if part.strip()
        )


def _extract_visible_text(html):
    parser = _VisibleTextExtractor()
    parser.feed(html)
    parser.close()
    return parser.title, parser.visible_text


# Phrases confirmed against a real captured Akamai/WAF edge-block page
# (data/fixtures/naukri/blocked_access_denied.html). Deliberately
# specific multi-word phrases (or a distinctive domain string), not
# single generic words, to avoid matching ordinary page content.
_UNKNOWN_BLOCK_PHRASES = [
    "access denied",
    "you don't have permission to access",
    "request blocked",
    "unusual traffic",
    "edgesuite.net",
]


def detect_block_reason(html: str) -> BlockReason:
    """
    Classify a captured page as blocked or not, based only on
    human-visible text (title + body) -- never on raw HTML source,
    tag attributes, CSS, or script content.

    Currently distinguishes only NONE vs UNKNOWN_BLOCK: the one
    category with both a genuine positive fixture
    (blocked_access_denied.html) and genuine negative fixtures
    (homepage.html, search_results_sre_bengaluru.html) to validate
    against.

    LOGIN_WALL, CAPTCHA, VERIFICATION, MAINTENANCE, TIMEOUT,
    RATE_LIMITED, and NETWORK_ERROR are intentionally NOT detected by
    this function yet -- no real Naukri page demonstrating any of them
    has been captured. Guessing at keyword rules for conditions never
    actually observed is exactly how an earlier naive, raw-HTML
    keyword check produced false positives on this same search-results
    fixture. This is a documented gap, not a silent one: extend this
    function only once a genuine fixture for a given condition exists.
    """
    title, visible_text = _extract_visible_text(html)
    combined = f"{title} {visible_text}".lower()

    if any(phrase in combined for phrase in _UNKNOWN_BLOCK_PHRASES):
        return BlockReason.UNKNOWN_BLOCK

    return BlockReason.NONE


def _find_matched_unknown_block_phrase(html: str):
    """
    Diagnostic-only companion to detect_block_reason(): returns the
    specific phrase from _UNKNOWN_BLOCK_PHRASES that matched (or None
    if none did), purely so a caller can log/report WHICH phrase
    triggered a BLOCKED classification -- never used to change
    classification behavior itself, and detect_block_reason()'s own
    signature/behavior is unchanged by this. Re-extracts the same
    visible text independently rather than sharing state, so both
    functions remain correct and callable on their own.
    """
    title, visible_text = _extract_visible_text(html)
    combined = f"{title} {visible_text}".lower()

    for phrase in _UNKNOWN_BLOCK_PHRASES:
        if phrase in combined:
            return phrase

    return None


class SearchPageState(Enum):
    """
    Deterministic classification of a Naukri search-results page,
    distinguishing a genuinely empty search from every other reason a
    search page might show zero parseable job-listing links.

    VALID_RESULTS
        Recognized search-results page with one or more job-listing
        links.

    VALID_EMPTY_RESULT
        Recognized search-results page (its own app-shell structural
        markers are present) with zero job-listing links -- a
        legitimate "this search found nothing" result.

    BLOCKED
        detect_block_reason() matched a known access-denied/WAF phrase.

    SOFT_BLOCK_OR_CHALLENGE
        Reachable, not a recognized access-denied page, but also not a
        recognized search-results shell -- and shows plain-language
        evidence of a challenge/interstitial page.

    PARSE_FAILURE
        Reachable, not blocked, not a recognized search-results shell,
        and shows no challenge/interstitial evidence either. The
        "none of the above" bucket: page structure could not be
        confidently classified.

    The safety invariant this whole classification exists to enforce:
    a page can only be VALID_EMPTY_RESULT when its own structural shell
    markers positively confirm it as Naukri's real search-results
    template. Zero job-listing links alone is never sufficient --
    SOFT_BLOCK_OR_CHALLENGE and PARSE_FAILURE also have zero links, and
    must never be silently reinterpreted as a legitimate empty result.
    """

    VALID_RESULTS = "VALID_RESULTS"
    VALID_EMPTY_RESULT = "VALID_EMPTY_RESULT"
    BLOCKED = "BLOCKED"
    SOFT_BLOCK_OR_CHALLENGE = "SOFT_BLOCK_OR_CHALLENGE"
    PARSE_FAILURE = "PARSE_FAILURE"


@dataclass
class SearchPageClassification:
    state: SearchPageState
    job_link_count: int = 0
    has_shell_markers: bool = False
    block_reason: BlockReason = BlockReason.NONE
    job_links: list = field(default_factory=list)
    detail: str = ""


# DOM element ids confirmed present, by direct inspection, on the real
# captured search_results_sre_bengaluru.html fixture -- rendered by
# Naukri's own search-results app shell regardless of how many (if
# any) job listings that search returns. Requiring ALL of them,
# checked against raw markup (these are structural id attributes, not
# human-visible prose, so the visible-text-only rule that guards
# detect_block_reason() does not apply here), is what lets
# classify_search_page() trust a zero-link page as a genuine empty
# result rather than an unrelated page that merely lacks job links.
_SEARCH_SHELL_MARKERS = ['id="jobs-list-header"', 'id="listContainer"']

# Checked only against extracted VISIBLE TEXT (see
# _extract_visible_text()), never raw markup -- for the same
# false-positive reason documented on detect_block_reason() and
# _UNKNOWN_BLOCK_PHRASES above. No genuine Naukri challenge/
# interstitial page has been captured; these phrases describe the
# generic class of "checking your browser" style interstitial shown by
# common bot-mitigation providers, confirmed to NOT appear on any of
# this project's real captured fixtures (homepage.html,
# search_results_sre_bengaluru.html, detail_valid.html,
# blocked_access_denied.html). This is a documented, deliberately
# narrow first cut -- extend it only once a genuine Naukri challenge
# page is captured, exactly as detect_block_reason()'s own docstring
# already asks for LOGIN_WALL/CAPTCHA/etc.
_CHALLENGE_PHRASES = [
    "checking your browser",
    "please wait while we verify",
    "verify you are human",
    "verifying you are human",
    "enable javascript and cookies to continue",
    "this process is automatic",
    "your browser will redirect",
]


# Strips HTML comments before the shell-marker check below. Without
# this, a documentation/explanatory HTML comment that happens to
# mention one of the marker strings verbatim (as this project's own
# synthetic test fixtures do, to document what they represent) would
# be indistinguishable from the marker actually appearing in real
# rendered markup -- the same class of false positive
# detect_block_reason() already guards against for <script>/<style>
# content, applied here to <!-- --> comments instead.
_HTML_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)


def _has_search_shell_markers(html: str) -> bool:
    stripped = _HTML_COMMENT_PATTERN.sub("", html)
    return all(marker in stripped for marker in _SEARCH_SHELL_MARKERS)


def _has_challenge_evidence(visible_text_lower: str) -> bool:
    return any(phrase in visible_text_lower for phrase in _CHALLENGE_PHRASES)


def classify_search_page(html: str) -> SearchPageClassification:
    """
    Classify a fetched Naukri search-results page into exactly one
    SearchPageState. See SearchPageState's docstring for the full
    state contract and the safety invariant this function enforces.

    Order of checks (each one only reached if the prior one did not
    match):
      1. detect_block_reason() -- a known access-denied/WAF phrase.
      2. One or more job-listing links parsed -> VALID_RESULTS.
      3. Zero links, but the search-results shell markers are present
         -> VALID_EMPTY_RESULT.
      4. Zero links, no shell markers, but challenge/interstitial
         evidence in the visible text -> SOFT_BLOCK_OR_CHALLENGE.
      5. None of the above -> PARSE_FAILURE.
    """
    block_reason = detect_block_reason(html)
    if block_reason != BlockReason.NONE:
        matched_phrase = _find_matched_unknown_block_phrase(html)
        detail = f"block phrase matched ({block_reason.value})"
        if matched_phrase is not None:
            detail += f": {matched_phrase!r}"
        return SearchPageClassification(
            state=SearchPageState.BLOCKED,
            block_reason=block_reason,
            detail=detail,
        )

    job_links = parse_search_results(html)
    job_link_count = len(job_links)
    has_shell = _has_search_shell_markers(html)

    if job_link_count > 0:
        return SearchPageClassification(
            state=SearchPageState.VALID_RESULTS,
            job_link_count=job_link_count,
            has_shell_markers=has_shell,
            job_links=job_links,
        )

    if has_shell:
        return SearchPageClassification(
            state=SearchPageState.VALID_EMPTY_RESULT,
            job_link_count=0,
            has_shell_markers=True,
            job_links=[],
            detail="zero job-listing links but search-results shell markers present",
        )

    _, visible_text = _extract_visible_text(html)
    if _has_challenge_evidence(visible_text.lower()):
        return SearchPageClassification(
            state=SearchPageState.SOFT_BLOCK_OR_CHALLENGE,
            job_link_count=0,
            has_shell_markers=False,
            detail="reachable but shows challenge/interstitial evidence, no search-results shell",
        )

    return SearchPageClassification(
        state=SearchPageState.PARSE_FAILURE,
        job_link_count=0,
        has_shell_markers=False,
        detail="page structure not recognized as search-results, block, or challenge",
    )


class _JobListingLinkExtractor(HTMLParser):
    """
    Extracts unique job-listing anchor links from a Naukri search
    results page: every <a> tag whose href contains "job-listings",
    keyed by href, with the anchor's own visible text content as the
    title.

    Selector and title source both match precedent already proven
    against a live page: the href*="job-listings" pattern from
    naukri_job_structure_probe.js, and using the anchor's innerText
    (not its title= attribute) as the label, as both
    naukri_job_structure_probe.js and naukri_manual_capture.js do.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._results = {}
        self._current_href = None
        self._current_text = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return

        href = dict(attrs).get("href", "")

        if "job-listings" in href:
            self._current_href = href
            self._current_text = []

    def handle_data(self, data):
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if tag != "a" or self._current_href is None:
            return

        title = "".join(self._current_text)
        title = re.sub(r"\s+", " ", title).strip()

        if self._current_href not in self._results:
            self._results[self._current_href] = title

        self._current_href = None
        self._current_text = []

    @property
    def results(self):
        return [
            {"title": title, "job_url": href}
            for href, title in self._results.items()
        ]


def parse_search_results(html: str) -> list:
    """
    Parse a Naukri search-results page into a minimal list of
    {"title": str, "job_url": str} entries, one per unique
    job-listing link, in first-seen order. Deduplicated by href,
    matching the dedup behavior already proven by
    naukri_manual_capture.js's `new Map(jobs.map(j => [j.url, j]))`
    approach.

    This is not a full RawJob: a search-results card carries no
    JD/experience/skills -- those only exist on the detail page.
    """
    parser = _JobListingLinkExtractor()
    parser.feed(html)
    parser.close()
    return parser.results


class _JsonLdBlockExtractor(HTMLParser):
    """
    Structurally extracts the text content of every
    <script type="application/ld+json"> element on the page (via
    HTMLParser, not regex over markup). Naukri embeds a schema.org
    JobPosting record here with clean, structured
    title/hiringOrganization/jobLocation/skills/description fields --
    a far more reliable source than chasing CSS selectors across the
    visible DOM, and confirmed present against the real captured
    fixture (data/fixtures/naukri/detail_valid.html).
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._in_target_script = False
        self._buf = []
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type") == "application/ld+json":
            self._in_target_script = True
            self._buf = []

    def handle_data(self, data):
        if self._in_target_script:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._in_target_script:
            self.blocks.append("".join(self._buf))
            self._in_target_script = False


def _extract_job_posting(html):
    """
    Return the parsed schema.org JobPosting dict from the page's
    JSON-LD, or None if no such block is present.
    """
    parser = _JsonLdBlockExtractor()
    parser.feed(html)
    parser.close()

    for block in parser.blocks:
        try:
            data = json.loads(block)
        except ValueError:
            continue

        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data

    return None


# Applied only to already-extracted plain visible text (never to raw
# HTML markup), for the two fields Naukri renders to humans as
# relative/range phrasing rather than structured JSON-LD data. The
# lookahead boundary stops each match at the next "Label:" token
# (e.g. "Posted: Few hours ago Openings: 1" -> "Posted: Few hours
# ago"), verified against the real captured fixture.
_POSTED_DATE_PATTERN = re.compile(
    r"Posted:\s*(.+?)(?=\s+[A-Z][a-zA-Z]*:|$)"
)
_EXPERIENCE_RANGE_PATTERN = re.compile(
    r"\b\d+\s*-\s*\d+\s*years\b", re.IGNORECASE
)


def parse_detail_page(html: str, job_url: str) -> dict:
    """
    Parse a Naukri job-detail page into a dict matching the RawJob
    field contract.

    Source of truth for title, company, location, skills, and jd_text
    is the page's own embedded schema.org JobPosting JSON-LD block
    (extracted structurally via HTMLParser, not regex over markup) --
    clean, structured data rather than DOM-selector heuristics.

    posted_date and experience_required are deliberately NOT sourced
    from the JSON-LD, which represents them as an absolute ISO
    "datePosted" date and an integer
    "experienceRequirements.monthsOfExperience" respectively. The
    page's own human-facing visible text carries a different,
    relative/range representation ("Posted: Few hours ago",
    "8 - 10 years") that the rest of this pipeline expects. Both
    representations are genuinely present on the real page; this is a
    deliberate choice of which real representation to surface, not an
    invented value.

    mandatory_skills is always []: the source page exposes exactly one
    "Key Skills" list (mirrored in JSON-LD as a single flat "skills"
    array), with no mandatory/preferred distinction on the page
    itself. All verified skills go into preferred_skills.
    """
    posting = _extract_job_posting(html) or {}

    title = posting.get("title") or ""

    hiring_org = posting.get("hiringOrganization") or {}
    company = hiring_org.get("name") or ""

    job_location = posting.get("jobLocation") or {}
    address = job_location.get("address") or {}
    locality = address.get("addressLocality") or []
    if isinstance(locality, str):
        locality = [locality]
    location = ", ".join(locality)

    description_html = posting.get("description") or ""
    # Reuse the same visible-text extractor used for block detection,
    # rather than a second parsing approach, to strip the <p>/<br/>/
    # <ul><li> markup embedded inside the JSON-LD description string.
    _, jd_text = _extract_visible_text(description_html)
    jd_text = jd_text.strip()

    skills = posting.get("skills") or []
    if not isinstance(skills, list):
        skills = []
    preferred_skills = [str(skill) for skill in skills]

    # Structured MINIMUM experience only -- schema.org's
    # experienceRequirements.monthsOfExperience represents a floor
    # ("at least this many months"), never a ceiling. 96 months on the
    # real fixture means "at least 8 years", not "up to 8 years"; the
    # maximum (if any) only ever comes from the visible "8 - 10 years"
    # text below, never from this field.
    experience_requirements = posting.get("experienceRequirements") or {}
    months_raw = experience_requirements.get("monthsOfExperience")
    try:
        experience_min_months = (
            int(months_raw) if months_raw is not None else None
        )
    except (TypeError, ValueError):
        experience_min_months = None

    _, visible_text = _extract_visible_text(html)

    posted_match = _POSTED_DATE_PATTERN.search(visible_text)
    posted_date = posted_match.group(0).strip() if posted_match else ""

    experience_match = _EXPERIENCE_RANGE_PATTERN.search(visible_text)
    experience_required = (
        experience_match.group(0).strip() if experience_match else ""
    )

    return {
        "source": "NAUKRI",
        "company": company,
        "title": title,
        "location": location,
        "work_model": "",
        "job_url": job_url,
        "application_url": job_url,
        "posted_date": posted_date,
        "jd_text": jd_text,
        "experience_required": experience_required,
        "mandatory_skills": [],
        "preferred_skills": preferred_skills,
        "experience_min_months": experience_min_months,
    }
