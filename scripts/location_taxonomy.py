#!/usr/bin/env python3

"""
Candidate-agnostic location and work-model taxonomy.

This module has no knowledge of any specific candidate, of config/profile.json, or of any
specific candidate. It defines:

  - a structured representation of a single normalized location
    (NormalizedLocation / LocationKind),
  - a structured representation of a normalized work model
    (WorkModel),
  - parsing of raw job-posting location/work-model text into these
    structures (parse_locations, parse_job_location_and_work_model),
  - and a pairwise/aggregate eligibility comparison between a job's
    location(s) and an arbitrary candidate's location preferences
    (assess_location_eligibility), returning an explicit
    LocationAssessment (MATCH / NO_MATCH / UNKNOWN) -- never a bare
    True/False, and UNKNOWN is never silently discarded.

Candidate-specific data (candidate_profile["target_locations"]) is
supplied by the caller to assess_location_eligibility(); nothing here is
specific to any one candidate's preferences.
"""

import re
from dataclasses import dataclass, field
from enum import Enum


class LocationKind(Enum):
    CITY = "CITY"
    COUNTRY = "COUNTRY"
    REGION = "REGION"
    PAN_COUNTRY = "PAN_COUNTRY"
    REMOTE_COUNTRY = "REMOTE_COUNTRY"
    REMOTE_GLOBAL = "REMOTE_GLOBAL"
    # Beyond the spec's minimum state list: a bare "Remote" with no
    # stated country/global scope. Distinct from UNKNOWN (which means
    # "could not classify at all") -- "Remote" alone unambiguously means
    # *some* form of remote work, it is only the scope that is
    # unstated. Needed so a candidate's own bare "Remote" preference
    # (see assess_location_eligibility) can be represented as "an
    # explicit remote preference, not a city" rather than as UNKNOWN or
    # forced into REMOTE_GLOBAL (which this module must never assume --
    # see parse_locations()).
    REMOTE_UNSPECIFIED = "REMOTE_UNSPECIFIED"
    UNKNOWN = "UNKNOWN"


class WorkModel(Enum):
    REMOTE = "REMOTE"
    HYBRID = "HYBRID"
    ONSITE = "ONSITE"
    UNKNOWN = "UNKNOWN"


class LocationEligibility(Enum):
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    UNKNOWN = "UNKNOWN"


@dataclass
class NormalizedLocation:
    kind: LocationKind
    city: str | None = None
    region: str | None = None
    country: str | None = None
    raw: str = ""


@dataclass
class LocationAssessment:
    eligibility: LocationEligibility
    job_locations: list = field(default_factory=list)
    work_model: WorkModel = WorkModel.UNKNOWN
    reason: str = ""


# ---------------------------------------------------------------------
# Normalization data. Intentionally the only place city/country/region
# aliases live -- do not duplicate this knowledge elsewhere.
# ---------------------------------------------------------------------

# "Do not assume Bangalore and Bengaluru are different locations" --
# every known alias for a city canonicalizes to one spelling plus its
# country. Only cities explicitly established by this project's own
# configuration (config/profile.json target_locations, config/
# searches.json locations, CLAUDE.md) or explicitly given in this
# module's own spec (including its deliberate India-coverage expansion)
# are included -- no invented geography, and no alias added unless it
# is an unambiguous, well-established name for the same city (a twin/
# sister city commonly conflated in job postings, an alternate English
# transliteration, or a colloquial/former name).
_CITY_ALIASES = {
    "bangalore": ("Bengaluru", "India"),
    "bengaluru": ("Bengaluru", "India"),
    "bangalore urban": ("Bengaluru", "India"),
    "hyderabad": ("Hyderabad", "India"),
    # Secunderabad is Hyderabad's twin city, forming one metro area and
    # routinely used interchangeably in job postings for this region.
    "secunderabad": ("Hyderabad", "India"),
    "pune": ("Pune", "India"),
    "chennai": ("Chennai", "India"),
    "mumbai": ("Mumbai", "India"),
    "bombay": ("Mumbai", "India"),
    "delhi": ("Delhi", "India"),
    "new delhi": ("Delhi", "India"),
    "noida": ("Noida", "India"),
    "greater noida": ("Noida", "India"),
    "gurugram": ("Gurugram", "India"),
    "gurgaon": ("Gurugram", "India"),
    "kolkata": ("Kolkata", "India"),
    "ahmedabad": ("Ahmedabad", "India"),
    "kochi": ("Kochi", "India"),
    "cochin": ("Kochi", "India"),
    "bhubaneswar": ("Bhubaneswar", "India"),
    "bhubaneshwar": ("Bhubaneswar", "India"),
    "dubai": ("Dubai", "UAE"),
    "abu dhabi": ("Abu Dhabi", "UAE"),
}

_COUNTRY_ALIASES = {
    "india": "India",
    "uae": "UAE",
    "united arab emirates": "UAE",
}

# Multi-word phrases that must be recognized as ONE location, never
# split by the multi-location list parser (e.g. "Delhi / NCR" is a
# single region, not two separate locations "Delhi" and "NCR").
# Looked up against the fully punctuation-normalized whole string only
# (see _clean()) -- "Delhi/NCR", "Delhi - NCR", "Delhi   NCR" and
# "Delhi / NCR" all normalize to the same key.
_COMPOUND_PHRASES = {
    "pan india": {"kind": LocationKind.PAN_COUNTRY, "country": "India"},
    "remote india": {"kind": LocationKind.REMOTE_COUNTRY, "country": "India"},
    "remote global": {"kind": LocationKind.REMOTE_GLOBAL},
    "global remote": {"kind": LocationKind.REMOTE_GLOBAL},
    "remote worldwide": {"kind": LocationKind.REMOTE_GLOBAL},
    "remote anywhere": {"kind": LocationKind.REMOTE_GLOBAL},
    "delhi ncr": {"kind": LocationKind.REGION, "region": "Delhi NCR", "country": "India"},
    # "NCR" (National Capital Region) is, in Indian job postings,
    # unambiguously shorthand for the Delhi NCR region -- not a
    # separate, standalone location.
    "ncr": {"kind": LocationKind.REGION, "region": "Delhi NCR", "country": "India"},
}

# Ordered so a more specific phrase is always tested before a shorter
# one it could otherwise be confused with (e.g. "work from office"
# before the generic "onsite"/"remote" catch-alls).
_WORK_MODEL_PHRASES = [
    ("work from home", WorkModel.REMOTE),
    ("wfh", WorkModel.REMOTE),
    ("work from office", WorkModel.ONSITE),
    ("wfo", WorkModel.ONSITE),
    ("on-site", WorkModel.ONSITE),
    ("onsite", WorkModel.ONSITE),
    ("on site", WorkModel.ONSITE),
    ("hybrid", WorkModel.HYBRID),
    ("remote", WorkModel.REMOTE),
]

# Trailing " - <phrase>" (or ", <phrase>") suffixes that are PURE work
# model words with no location meaning of their own -- safe to strip
# before location parsing (e.g. "Bengaluru - Hybrid" -> "Bengaluru").
# Deliberately excludes "remote"/"wfh"/"work from home": those DO carry
# location meaning (see parse_locations()'s REMOTE_* handling) and must
# never be stripped out of the location text.
_STRIPPABLE_SUFFIX_PATTERN = re.compile(
    r"\s*[-,]\s*(work\s*from\s*office|wfo|on[\s-]?site|hybrid)\s*$",
    re.IGNORECASE,
)

_LIST_SEPARATOR_PATTERN = re.compile(
    r"\s*(?:,|/|\||&|\band\b)\s*", re.IGNORECASE
)


def _clean(text):
    """Lowercase, punctuation-normalized, whitespace-collapsed form
    used only for dictionary lookups -- never returned to a caller."""
    text = str(text or "")
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"[()]", " ", text)
    text = re.sub(r"[\/\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def normalize_location_text(raw):
    """
    Classify a single, already-isolated location string (not a
    multi-location list -- see parse_locations() for that) into a
    NormalizedLocation.
    """
    original = str(raw or "").strip()

    if not original:
        return NormalizedLocation(kind=LocationKind.UNKNOWN, raw=original)

    cleaned = _clean(original)

    if cleaned in _COMPOUND_PHRASES:
        spec = _COMPOUND_PHRASES[cleaned]
        return NormalizedLocation(
            kind=spec["kind"],
            city=spec.get("city"),
            region=spec.get("region"),
            country=spec.get("country"),
            raw=original,
        )

    if cleaned in _CITY_ALIASES:
        city, country = _CITY_ALIASES[cleaned]
        return NormalizedLocation(
            kind=LocationKind.CITY, city=city, country=country, raw=original
        )

    if cleaned in _COUNTRY_ALIASES:
        return NormalizedLocation(
            kind=LocationKind.COUNTRY,
            country=_COUNTRY_ALIASES[cleaned],
            raw=original,
        )

    if cleaned == "remote":
        # Bare "Remote", no stated country or global scope. Never
        # upgraded to REMOTE_GLOBAL here -- see module docstring and
        # LocationKind.REMOTE_UNSPECIFIED.
        return NormalizedLocation(kind=LocationKind.REMOTE_UNSPECIFIED, raw=original)

    return NormalizedLocation(kind=LocationKind.UNKNOWN, raw=original)


def parse_locations(raw_text):
    """
    Parse a (possibly multi-location) raw string into a list of
    NormalizedLocation, one per explicitly listed location. Never
    collapses multiple cities into one. A known compound phrase (e.g.
    "Delhi / NCR", "PAN India", "Remote Global") is recognized as a
    single location before any list-separator splitting is attempted,
    so it is never incorrectly split apart.
    """
    original = str(raw_text or "").strip()

    if not original:
        return [NormalizedLocation(kind=LocationKind.UNKNOWN, raw=original)]

    cleaned_whole = _clean(original)

    if (
        cleaned_whole in _COMPOUND_PHRASES
        or cleaned_whole in _CITY_ALIASES
        or cleaned_whole in _COUNTRY_ALIASES
        or cleaned_whole == "remote"
    ):
        return [normalize_location_text(original)]

    parts = [p for p in _LIST_SEPARATOR_PATTERN.split(original) if p.strip()]

    if len(parts) <= 1:
        return [normalize_location_text(original)]

    return [normalize_location_text(p) for p in parts]


def normalize_work_model_text(text):
    """Classify a raw string for an explicit work-model signal. Never
    infers a work model from a bare city name -- only recognized
    work-model phrases (hybrid/onsite/remote/wfh/wfo/...) produce
    anything other than UNKNOWN."""
    cleaned = _clean(text)

    if not cleaned:
        return WorkModel.UNKNOWN

    for phrase, model in _WORK_MODEL_PHRASES:
        if _clean(phrase) in cleaned:
            return model

    return WorkModel.UNKNOWN


def _strip_non_remote_work_model_suffix(text):
    return _STRIPPABLE_SUFFIX_PATTERN.sub("", str(text or "")).strip()


def parse_job_location_and_work_model(location_text, work_model_text=""):
    """
    Parse a job's location field (and, if present, its separate
    work_model field) into (list[NormalizedLocation], WorkModel).

    If work_model_text is non-empty, it is the authoritative source for
    work model. Otherwise, location_text itself is scanned for an
    embedded work-model signal (e.g. "Bengaluru - Hybrid"), which is
    then stripped from the text used for location parsing -- unless the
    signal found is remote-related ("Remote"/"WFH"/"Work from home"),
    which is never stripped, since it also carries location meaning
    (see parse_locations()'s REMOTE_* handling of e.g. "Remote - India").
    """
    work_model_text = str(work_model_text or "").strip()

    if work_model_text:
        work_model = normalize_work_model_text(work_model_text)
    else:
        work_model = normalize_work_model_text(location_text)

    location_for_parsing = _strip_non_remote_work_model_suffix(location_text)
    locations = parse_locations(location_for_parsing)

    return locations, work_model


# ---------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------

_REMOTE_KINDS = (
    LocationKind.REMOTE_UNSPECIFIED,
    LocationKind.REMOTE_COUNTRY,
    LocationKind.REMOTE_GLOBAL,
)

_BROAD_PHYSICAL_KINDS = (LocationKind.COUNTRY, LocationKind.PAN_COUNTRY)


def _remote_pair_eligibility(job_loc, candidate_loc):
    """
    Remote-scope compatibility matrix. Revised so that a generic,
    unscoped "Remote" candidate preference (REMOTE_UNSPECIFIED) is never
    treated as an implicit "Remote Global" acceptance -- global remote
    must be explicitly stated by the candidate, never inferred. The
    full matrix (candidate kind x job kind), each cell chosen
    deterministically and documented rather than left to guess which of
    NO_MATCH/UNKNOWN "feels right" case by case:

                          job=UNSPECIFIED   job=COUNTRY(X)        job=GLOBAL
      cand=UNSPECIFIED    MATCH             MATCH                 NO_MATCH
      cand=COUNTRY(Y)     UNKNOWN           MATCH iff X==Y        UNKNOWN
                                             else NO_MATCH
      cand=GLOBAL         UNKNOWN           UNKNOWN               MATCH
    """
    if candidate_loc.kind == LocationKind.REMOTE_UNSPECIFIED:
        if job_loc.kind == LocationKind.REMOTE_GLOBAL:
            # A generic "Remote" preference does not authorize a
            # worldwide/global remote role -- global scope must be
            # explicit. Chosen deterministically as NO_MATCH (not
            # UNKNOWN): the candidate gave no signal at all towards
            # accepting work outside their own presumed country, which
            # this product treats the same as any other unaccepted
            # remote scope (see the CITY-only-candidate case above).
            return LocationEligibility.NO_MATCH
        # job is REMOTE_UNSPECIFIED or REMOTE_COUNTRY(X): a generic
        # "Remote" preference is compatible with a specific-country
        # remote job or another unscoped one -- it says nothing that
        # rules either out.
        return LocationEligibility.MATCH

    if candidate_loc.kind == LocationKind.REMOTE_COUNTRY:
        if job_loc.kind == LocationKind.REMOTE_COUNTRY:
            return (
                LocationEligibility.MATCH
                if candidate_loc.country == job_loc.country
                else LocationEligibility.NO_MATCH
            )
        # job is REMOTE_UNSPECIFIED (its scope isn't stated -- could be
        # the candidate's country or could be elsewhere) or REMOTE_
        # GLOBAL (do NOT automatically MATCH a country-scoped
        # preference against a global job just because global scope
        # technically includes that country -- genuinely ambiguous
        # without an explicit global preference, not a guessed pass).
        return LocationEligibility.UNKNOWN

    # candidate_loc.kind == REMOTE_GLOBAL
    if job_loc.kind == LocationKind.REMOTE_GLOBAL:
        return LocationEligibility.MATCH

    # job is REMOTE_UNSPECIFIED or REMOTE_COUNTRY(X): the candidate is
    # flexible about their own scope, but the job restricts itself to a
    # country (or an unstated one) that we cannot confirm the candidate
    # is eligible/located in from this data alone -- no visa/work-
    # authorization assumptions are made here.
    return LocationEligibility.UNKNOWN


def _physical_pair_eligibility(job_loc, candidate_loc):
    if job_loc.kind == LocationKind.CITY and candidate_loc.kind == LocationKind.CITY:
        return (
            LocationEligibility.MATCH
            if job_loc.city == candidate_loc.city
            else LocationEligibility.NO_MATCH
        )

    if job_loc.kind == LocationKind.REGION and candidate_loc.kind == LocationKind.REGION:
        return (
            LocationEligibility.MATCH
            if job_loc.region == candidate_loc.region
            else LocationEligibility.NO_MATCH
        )

    if job_loc.kind in _BROAD_PHYSICAL_KINDS or candidate_loc.kind in _BROAD_PHYSICAL_KINDS:
        # A country-wide (COUNTRY or PAN_COUNTRY) job/preference is
        # compatible with any physical location in the same country.
        if job_loc.country and candidate_loc.country:
            return (
                LocationEligibility.MATCH
                if job_loc.country == candidate_loc.country
                else LocationEligibility.NO_MATCH
            )
        return LocationEligibility.UNKNOWN

    # Any other physical-kind combination (e.g. CITY vs REGION): no
    # city-to-region geographic hierarchy is modeled in this component
    # (documented limitation) -- a conservative, definitive NO_MATCH
    # rather than a guess.
    return LocationEligibility.NO_MATCH


def _pair_eligibility(job_loc, candidate_loc):
    if job_loc.kind == LocationKind.UNKNOWN or candidate_loc.kind == LocationKind.UNKNOWN:
        return LocationEligibility.UNKNOWN

    job_is_remote = job_loc.kind in _REMOTE_KINDS
    candidate_is_remote = candidate_loc.kind in _REMOTE_KINDS

    if job_is_remote != candidate_is_remote:
        # A remote-type entry and a physical-location entry never
        # satisfy each other in this model.
        return LocationEligibility.NO_MATCH

    if job_is_remote:
        return _remote_pair_eligibility(job_loc, candidate_loc)

    return _physical_pair_eligibility(job_loc, candidate_loc)


def assess_location_eligibility(job, candidate_profile):
    """
    Assess whether `job`'s location(s)/work model are compatible with
    `candidate_profile`'s target_locations.

    `job` is a normalized job dict carrying "location" and, optionally,
    "work_model" (discover_local.normalize_job() output). `candidate_
    profile` is a candidate profile dict shaped like config/
    profile.json -- supplied explicitly by the caller, the same
    convention score_job() and assess_experience_eligibility() use.
    There is no default/global fallback and no specific-candidate data here.

    A job is location-compatible (MATCH) if ANY explicitly listed job
    location is compatible with ANY of the candidate's target
    locations. If no pairing MATCHes but at least one pairing is
    genuinely ambiguous, the overall result is UNKNOWN (never silently
    treated as NO_MATCH). Only when every pairing is a definitive
    mismatch is the result NO_MATCH.
    """
    job_locations, work_model = parse_job_location_and_work_model(
        job.get("location", ""), job.get("work_model", "")
    )

    candidate_raw_locations = candidate_profile.get("target_locations", [])
    candidate_locations = [
        normalize_location_text(loc) for loc in candidate_raw_locations
    ]

    if not candidate_locations:
        return LocationAssessment(
            eligibility=LocationEligibility.UNKNOWN,
            job_locations=job_locations,
            work_model=work_model,
            reason="Candidate profile has no target_locations configured.",
        )

    if all(loc.kind == LocationKind.UNKNOWN for loc in job_locations):
        return LocationAssessment(
            eligibility=LocationEligibility.UNKNOWN,
            job_locations=job_locations,
            work_model=work_model,
            reason=(
                f"Missing or unparseable job location: "
                f"{job.get('location', '')!r}."
            ),
        )

    results = set()
    matched_pair = None

    for job_loc in job_locations:
        for candidate_loc in candidate_locations:
            pair_result = _pair_eligibility(job_loc, candidate_loc)
            results.add(pair_result)

            if pair_result == LocationEligibility.MATCH and matched_pair is None:
                matched_pair = (job_loc, candidate_loc)

    job_raws = [loc.raw for loc in job_locations]
    candidate_raws = [loc.raw for loc in candidate_locations]

    if LocationEligibility.MATCH in results:
        eligibility = LocationEligibility.MATCH
        reason = (
            f"Job location '{matched_pair[0].raw}' matches candidate "
            f"target location '{matched_pair[1].raw}'."
        )
    elif LocationEligibility.UNKNOWN in results:
        eligibility = LocationEligibility.UNKNOWN
        reason = (
            f"Job location(s) {job_raws} could not be conclusively "
            f"compared against candidate target locations {candidate_raws}."
        )
    else:
        eligibility = LocationEligibility.NO_MATCH
        reason = (
            f"Excluded: job location(s) {job_raws} do not match any "
            f"candidate target location {candidate_raws}."
        )

    return LocationAssessment(
        eligibility=eligibility,
        job_locations=job_locations,
        work_model=work_model,
        reason=reason,
    )
