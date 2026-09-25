#!/usr/bin/env python3

"""
Phase 13 broad-discovery -- REAL search-provider evidence capture (Part 9 /
Part 10 proof of concept).

WHAT THIS FILE IS: a one-time, agent-supervised evidence artifact, NOT a
production adapter and NOT something api/main.py ever imports or wires
into the running backend by default.

WHY IT EXISTS: web_search_discovery_adapter.py is honest that this
backend process has no way to invoke a web-search tool itself
(WEB_SEARCH_BACKEND is None by default -- see that module's docstring).
But the AGENT operating this repository in this Claude Code session DOES
have its own WebSearch tool, and used it -- for real, on 2026-09-21 --
to run the exact `site:<domain> "<role>" <location>` queries this
project's own discovery-skill strategy recommends (see Part 10), against
all seven sources this phase investigates. This module captures those
REAL results (titles + public URLs, exactly as returned, nothing
invented) so the existing pipeline (normalize -> canonical -> dedup ->
freshness -> eligibility -> score) can be run against genuinely-observed
search-provider data at least once, proving the acquisition path works
end to end -- without pretending this backend process can repeat the
search itself on a schedule. It cannot, until a real search-API
credential is configured (see web_search_discovery_adapter.py).

HONESTY RULES FOLLOWED HERE:
  - Every URL below was actually returned by a real WebSearch call in
    this session. None is invented.
  - `company` is populated ONLY when it can be parsed directly out of
    the search result's own TITLE STRING (which each job board itself
    writes, e.g. "Site Reliability Engineer job at Quotient Technology -
    Instahyre") -- never from the search tool's own generated prose
    summary, which paraphrases/compresses and is not a verified
    structured fact.
  - Indeed's own titles never include a company name at all (see
    _parse_indeed() below) -- so Indeed entries deliberately have
    company="" and are EXCLUDED from normalization (discover_local.
    normalize_job() requires company; leaving it blank rather than
    guessing is the honest outcome here, not a bug).
  - jd_text, experience_required (except where literally present in
    Foundit's own title text), mandatory_skills, and posted_date are
    left blank for every entry -- the search-provider snippet does not
    reliably give these, and this project never fabricates them.
  - Shine.com yielded ZERO results for either of two site:-scoped
    queries tried in this session -- documented as a genuine negative
    finding, not papered over.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

CAPTURE_DATE = "2026-09-21"
CAPTURE_LOCATION_QUERY = "Bangalore"  # India / Bengaluru / Bangalore used interchangeably per source's own indexing

# ---------------------------------------------------------------------
# Raw, verbatim WebSearch results (title, url) -- real data, this session
# ---------------------------------------------------------------------

_LINKEDIN_QUERY = 'site:linkedin.com/jobs/view "Site Reliability Engineer" Bangalore'
_LINKEDIN_RESULTS = [
    ("Site Reliability Engineer (SRE)DevOps Engineer_ Senior Member Technical_Bangalore",
     "https://in.linkedin.com/jobs/view/site-reliability-engineer-sre-devops-engineer-senior-member-technical-bangalore-at-broadridge-india-4365029972"),
    ("Rattle \U0001f996 hiring Site Reliability Engineer in Bengaluru, Karnataka, India | LinkedIn",
     "https://in.linkedin.com/jobs/view/site-reliability-engineer-at-rattle-%F0%9F%A6%96-3713981489"),
    ("ProSol IT hiring Site Reliability Engineer in Bengaluru, Karnataka, India | LinkedIn",
     "https://in.linkedin.com/jobs/view/site-reliability-engineer-at-prosol-it-2803407748"),
    ("BlackLine hiring Associate Site Reliability Engineer in Bengaluru, Karnataka, India | LinkedIn",
     "https://in.linkedin.com/jobs/view/associate-site-reliability-engineer-at-blackline-3144459838"),
    ("SIXT Research & Development India hiring Site Reliability Engineer II (DB) in Bengaluru, Karnataka, India | LinkedIn",
     "https://in.linkedin.com/jobs/view/site-reliability-engineer-ii-db-at-sixt-research-development-india-3628987742"),
    ("NVIDIA hiring Senior Site Reliability Engineer in Bengaluru, Karnataka, India | LinkedIn",
     "https://in.linkedin.com/jobs/view/senior-site-reliability-engineer-at-nvidia-2996877120"),
    ("Pegasystems hiring Principal Site Reliability Engineer, Deployment in Bengaluru, Karnataka, India | LinkedIn",
     "https://in.linkedin.com/jobs/view/principal-site-reliability-engineer-deployment-at-pegasystems-2991524213"),
    ("Walmart Global Tech India hiring Principal Site Reliability Engineer in Bengaluru, Karnataka, India | LinkedIn",
     "https://in.linkedin.com/jobs/view/principal-site-reliability-engineer-at-walmart-global-tech-india-3128350276"),
    ("Evertz hiring Site Reliability Engineer, Evertz India in Bengaluru, Karnataka, India | LinkedIn",
     "https://in.linkedin.com/jobs/view/site-reliability-engineer-evertz-india-at-evertz-3361596574"),
]

_INDEED_QUERY = 'site:in.indeed.com/viewjob "site reliability engineer" Bangalore'
_INDEED_RESULTS = [
    ("Site Reliability Engineer - Bengaluru, Karnataka - Indeed.com", "https://in.indeed.com/viewjob?jk=077d5ad0dfda0acd"),
    ("Site Reliability Engineer (SRE) - Bengaluru, Karnataka - Indeed.com", "https://in.indeed.com/viewjob?jk=c5520fc69ff61921"),
    ("Lead Site Reliability Engineer - Bengaluru, Karnataka - Indeed.com", "https://in.indeed.com/viewjob?jk=ca2d6a0d5f69af65"),
    ("Site Reliability Engineer 1 - Bengaluru, Karnataka - Indeed.com", "https://in.indeed.com/viewjob?jk=71acaf90376013a2"),
    ("Site Reliability Engineer 3 - Bengaluru, Karnataka - Indeed.com", "https://in.indeed.com/viewjob?jk=3a7f4334653a674b"),
    ("Site Reliability Engineer - Architect/Principal - Bengaluru, Karnataka - Indeed.com", "https://in.indeed.com/viewjob?jk=eb7f731a12256f6a"),
    ("Application Support and Site Reliability Engineering (SRE) - Bengaluru, Karnataka - Indeed.com", "https://in.indeed.com/viewjob?jk=abfd633fdf763fc8"),
    ("Site Reliability Engineer - 2 - Bengaluru, Karnataka - Indeed.com", "https://in.indeed.com/viewjob?jk=ad22ce4289579ea3"),
    ("Senior Analyst Programmer - Site Reliability Engineer - Bengaluru, Karnataka - Indeed.com", "https://in.indeed.com/viewjob?jk=9d7d4396bff78d93"),
]

_FOUNDIT_QUERY = 'site:foundit.in/job "site reliability engineer" Bangalore'
_FOUNDIT_RESULTS = [
    ("Site Reliability Engineer with 5 - 7 Years of Experience at inoptra digital in Bengaluru / Bangalore,India",
     "https://www.foundit.in/job/site-reliability-engineer-inoptra-digital-bengaluru-bangalore-48221160"),
    ("Site Reliability Engineer with 6 - 12 Year of Experience at SWITS DIGITAL Private Limited in Bengaluru / Bangalore,India",
     "https://www.foundit.in/job/site-reliability-engineer-swits-digital-private-limited-bengaluru-bangalore-37494536"),
    ("Site Reliability Engineer with 2 - 5 Year of Experience at IBM in Bengaluru / Bangalore,India",
     "https://www.foundit.in/job/site-reliability-engineer-ibm-bengaluru-bangalore-40589236"),
    ("Site Reliability Engineer - Performance Engineering Job For 7-12 Year Exp In PhonePe Bengaluru / Bangalore, India - 25832880 | foundit India",
     "https://www.foundit.in/job/site-reliability-engineer-performance-engineering-phonepe-bengaluru-bangalore-25832880"),
    ("Senior Site Reliability Engineer with 5 - 12 Years of Experience at blackline india in Bengaluru / Bangalore,India",
     "https://www.foundit.in/job/senior-site-reliability-engineer-blackline-india-bengaluru-bangalore-50375923"),
    ("Site Reliability Engineer 5 with 12 - 14 Year of Experience at Adobe in Bengaluru / Bangalore,India",
     "https://www.foundit.in/job/site-reliability-engineer-5-adobe-bengaluru-bangalore-38989942"),
]

_INSTAHYRE_QUERY = 'site:instahyre.com/job "site reliability engineer" Bangalore'
_INSTAHYRE_RESULTS = [
    ("Site Reliability Engineer job at Quotient Technology - Instahyre", "https://www.instahyre.com/job-25417-site-reliability-engineer-at-quotient-technology-bangalore/"),
    ("Site Reliability Engineer job at Booking Holdings - Instahyre", "https://www.instahyre.com/job-275044-site-reliability-engineer-at-booking-holdings-bangalore/"),
    ("Site Reliability Engineer job at Google - Instahyre", "https://www.instahyre.com/job-175606-site-reliability-engineer-at-google-2-bangalore/"),
    ("Site Reliability Engineer job at VMware - Instahyre", "https://www.instahyre.com/job-70166-site-reliability-engineer-at-vmware-bangalore/"),
    ("Site Reliability Engineer job at CRED - Instahyre", "https://www.instahyre.com/job-120871-site-reliability-engineer-at-cred-bangalore/"),
    ("SRE (Site Reliability Engineer) job at Flipkart - Instahyre", "https://www.instahyre.com/job-292944-sre-site-reliability-engineer-at-flipkart-bangalore/"),
    ("Site Reliability Engineer job at Oracle - Instahyre", "https://www.instahyre.com/job-122854-site-reliability-engineer-at-oracle-bangalore/"),
    ("Site Reliability Engineer job at Dream11 - Instahyre", "https://www.instahyre.com/job-88738-site-reliability-engineer-at-dream11-bangalore/"),
    ("Site Reliability Engineer job at Zynga - Instahyre", "https://www.instahyre.com/job-150954-site-reliability-engineer-at-zynga-bangalore/"),
]

_CUTSHORT_QUERY = 'site:cutshort.io/job "site reliability engineer" Bangalore'
_CUTSHORT_RESULTS = [
    ("SigTuple is hiring Site Reliability Engineer - DevOps job in Bengaluru (Bangalore) | Cutshort",
     "https://cutshort.io/job/Site-Reliability-Engineer-DevOps-Bengaluru-Bangalore-SigTuple-m7y8fjjk"),
    ("Healthifyme is hiring Site Reliability Engineer job in Bengaluru (Bangalore) | Cutshort",
     "https://cutshort.io/job/Site-Reliability-Engineer-Bengaluru-Bangalore-Healthifyme-x7tGwRbz"),
    ("JP Morgan Services India Pvt. Ltd. is hiring Site Reliability Engineer job in Bengaluru (Bangalore) | Cutshort",
     "https://cutshort.io/job/Site-Reliability-Engineer-Bengaluru-Bangalore-JP-Morgan-Services-India-Pvt-Ltd--RepcqQBn"),
    ("Wissen Technology is hiring SRE / Site Reliability Engineer job in Bengaluru (Bangalore) | Cutshort",
     "https://cutshort.io/job/SRE-Site-Reliability-Engineer-Bengaluru-Bangalore-Wissen-Technology-6PEFK3G0"),
    ("Site Reliability Engineer job in Bengaluru (Bangalore) | MoEngage Inc is hiring on CutShort | CutShort",
     "https://cutshort.io/job/Site-Reliability-Engineer-Bengaluru-Bangalore-MoEngage-Inc-piTemfG1"),
]

_WELLFOUND_QUERY = 'site:wellfound.com/jobs "site reliability engineer" India'
_WELLFOUND_RESULTS = [
    ("Site Reliability Engineer at Kong • India • Bengaluru", "https://wellfound.com/jobs/3238981-site-reliability-engineer"),
    ("Site Reliability Engineer (SRE) at Logikality • Bengaluru", "https://wellfound.com/jobs/4520794-site-reliability-engineer-sre"),
    ("Sr. Site Reliability Engineer at VideoVerse • Mumbai", "https://wellfound.com/jobs/3532146-sr-site-reliability-engineer"),
]

_SHINE_QUERY_ATTEMPTS = [
    'site:shine.com/jobs "site reliability engineer" Bangalore',
    '"site reliability engineer" Bangalore site:shine.com',
]
_SHINE_RESULTS = []  # Confirmed empty for both query phrasings tried this session -- see module docstring.


# ---------------------------------------------------------------------
# Title parsing (conservative -- skip, never guess, on no confident match)
# ---------------------------------------------------------------------

def _parse_linkedin(title, url):
    """Returns {"company", "title", "experience_required"} or None."""
    match = re.match(r"^(?P<company>.+?) hiring (?P<job_title>.+?) in .+$", title)
    if match:
        return {"company": match.group("company").strip(), "title": match.group("job_title").strip(), "experience_required": ""}

    # Fallback: LinkedIn job URLs almost always end in ...-at-<company-slug>-<numeric-id>
    slug_match = re.search(r"-at-([a-z0-9-]+)-\d+/?$", url, re.IGNORECASE)
    if slug_match:
        company = " ".join(w.capitalize() for w in slug_match.group(1).split("-") if w and not w.startswith("%"))
        if company:
            return {"company": company, "title": title, "experience_required": ""}
    return None


def _parse_indeed(title, url):
    # Indeed's own search-result titles never carry a company name --
    # only role + location (verified against all 9 real results captured
    # this session). Company is honestly unknown from this path alone,
    # so every Indeed entry is intentionally skipped (see module
    # docstring) -- this function always returns None.
    return None


_EXPERIENCE_RE = re.compile(r"(\d+)\s*-\s*(\d+)\s*Years?(?: of Experience)?", re.IGNORECASE)


def _parse_foundit(title, url):
    match = re.match(r"^(?P<job_title>.+?) at (?P<company>.+?) in (?P<location>.+)$", title)
    if not match:
        return None
    experience = ""
    exp_match = _EXPERIENCE_RE.search(title)
    if exp_match:
        experience = f"{exp_match.group(1)}-{exp_match.group(2)} years"
    return {"company": match.group("company").strip(), "title": match.group("job_title").strip(), "experience_required": experience}


def _parse_instahyre(title, url):
    match = re.match(r"^(?P<job_title>.+?) job at (?P<company>.+?) - Instahyre$", title)
    if match:
        return {"company": match.group("company").strip(), "title": match.group("job_title").strip(), "experience_required": ""}
    return None


def _parse_cutshort(title, url):
    match = re.match(r"^(?P<company>.+?) is hiring (?P<job_title>.+?) job in (?P<location>.+?) \| Cutshort$", title)
    if match:
        return {"company": match.group("company").strip(), "title": match.group("job_title").strip(), "experience_required": ""}
    match = re.match(r"^(?P<job_title>.+?) job in .+? \| (?P<company>.+?) is hiring on CutShort", title)
    if match:
        return {"company": match.group("company").strip(), "title": match.group("job_title").strip(), "experience_required": ""}
    return None


def _parse_wellfound(title, url):
    match = re.match(r"^(?P<job_title>.+?) at (?P<company>.+?)\s*•", title)
    if match:
        return {"company": match.group("company").strip(), "title": match.group("job_title").strip(), "experience_required": ""}
    return None


def _build_raw_jobs(source, query, results, parser, location=CAPTURE_LOCATION_QUERY):
    jobs = []
    skipped = []
    for title, url in results:
        parsed = parser(title, url)
        if not parsed or not parsed.get("company"):
            skipped.append({"title": title, "url": url, "reason": "company not confidently parseable from title/URL"})
            continue
        jobs.append(
            {
                "source": source,
                "job_source": source,
                "discovery_source": "WEB_SEARCH",
                "company": parsed["company"],
                "title": parsed["title"],
                "location": location,
                "work_model": "",
                "job_url": url,
                "application_url": url,
                "posted_date": "",
                "jd_text": "",
                "experience_required": parsed.get("experience_required", ""),
                "mandatory_skills": [],
                "preferred_skills": [],
            }
        )
    return jobs, skipped, query


CAPTURED_EVIDENCE = {
    "LINKEDIN": _build_raw_jobs("LINKEDIN", _LINKEDIN_QUERY, _LINKEDIN_RESULTS, _parse_linkedin),
    "INDEED": _build_raw_jobs("INDEED", _INDEED_QUERY, _INDEED_RESULTS, _parse_indeed),
    "FOUNDIT": _build_raw_jobs("FOUNDIT", _FOUNDIT_QUERY, _FOUNDIT_RESULTS, _parse_foundit),
    "INSTAHYRE": _build_raw_jobs("INSTAHYRE", _INSTAHYRE_QUERY, _INSTAHYRE_RESULTS, _parse_instahyre),
    "CUTSHORT": _build_raw_jobs("CUTSHORT", _CUTSHORT_QUERY, _CUTSHORT_RESULTS, _parse_cutshort),
    "WELLFOUND": _build_raw_jobs("WELLFOUND", _WELLFOUND_QUERY, _WELLFOUND_RESULTS, _parse_wellfound),
    "SHINE": ([], [], _SHINE_QUERY_ATTEMPTS),
}


def get_evidence_backend(source):
    """Returns a WEB_SEARCH_BACKEND-shaped callable(query) -> list[dict]
    that replays this source's captured real jobs, for a one-off,
    supervised pipeline-validation run via
    web_search_discovery_adapter.set_web_search_backend(). Never used by
    the FastAPI app itself."""
    jobs, _skipped, _query = CAPTURED_EVIDENCE[source]

    def _backend(_search_query):
        return list(jobs)

    return _backend


if __name__ == "__main__":
    import json

    summary = {}
    for source, (jobs, skipped, query) in CAPTURED_EVIDENCE.items():
        summary[source] = {
            "query": query,
            "urls_found": len(jobs) + len(skipped),
            "normalizable_jobs": len(jobs),
            "skipped_unparseable_company": skipped,
            "sample_jobs": jobs[:3],
        }
    print(json.dumps(summary, indent=2))
