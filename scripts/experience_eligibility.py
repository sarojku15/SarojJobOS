#!/usr/bin/env python3

"""
Candidate-specific experience eligibility.

Prevents jobs whose required experience is clearly below a candidate's
actual experience from being treated as normal recommended matches,
without silently discarding jobs whose experience requirement could not
be reliably determined.

This module does no HTML scraping and makes no assumptions about which
job source produced its input -- it operates only on the two
already-extracted fields a normalized job dict may carry:

  - experience_required (str): the human-facing range/phrase, e.g.
    "8 - 10 years" -- already isolated to the job's own header field by
    the upstream parser (see naukri_parser.parse_detail_page), never
    raw page text. This module never re-scans a full page itself, so it
    structurally cannot pick up an unrelated number from a "Similar
    Jobs" card, a footer, or company boilerplate.
  - experience_min_months (int | None): a structured minimum, currently
    sourced from Naukri's schema.org JobPosting JSON-LD
    (experienceRequirements.monthsOfExperience). This value is a FLOOR
    ("at least this many months"), never a ceiling -- Naukri's JSON-LD
    does not expose a structured maximum, so the maximum always comes
    from experience_required when one is present.
"""

import re
from dataclasses import dataclass
from enum import Enum


class Eligibility(Enum):
    MATCH = "MATCH"
    BELOW_PROFILE = "BELOW_PROFILE"
    BORDERLINE = "BORDERLINE"
    ABOVE_PROFILE = "ABOVE_PROFILE"
    UNKNOWN = "UNKNOWN"


@dataclass
class ExperienceAssessment:
    eligibility: Eligibility
    parsed_min_years: float | None
    parsed_max_years: float | None
    candidate_experience_years: float | None
    source_min_experience_months: int | None
    is_suspicious: bool
    reason: str
    requirement_type: str = "UNKNOWN"


# Order matters: a range must be tried before a bare "N years" pattern,
# otherwise "8-10 years" would falsely short-circuit on its own "10
# years" tail. Applied only to the caller-supplied experience_required
# string -- never to a full page or JD body -- so there is nothing here
# that scans "unrelated numbers elsewhere on a job page."
_YEAR_UNIT = r"(?:years?|yrs?\.?)"
_DASH = r"[-–—]"  # hyphen, en dash, em dash -- all seen on real JDs

# The unit is OPTIONAL for a range or a "+" figure -- "10-12" and "10+"
# are unambiguously experience-shaped even without the word "years"
# because this pattern only ever runs against the isolated
# experience_required field, never free JD text. A bare single number
# ("10" alone) is NOT treated as years without an explicit unit --
# too easy to confuse with an unrelated figure.
_RANGE_PATTERN = re.compile(
    rf"(\d+(?:\.\d+)?)\s*(?:{_DASH}|\bto\b)\s*(\d+(?:\.\d+)?)\s*{_YEAR_UNIT}?",
    re.IGNORECASE,
)
_PLUS_PATTERN = re.compile(
    rf"(\d+(?:\.\d+)?)\s*\+\s*{_YEAR_UNIT}?", re.IGNORECASE
)
# "minimum 10 years" / "at least 10 years" / "10 years or more" all mean
# an open-ended floor, same as "10+" -- NOT a fixed single value of 10.
_OPEN_ENDED_MIN_PATTERN = re.compile(
    rf"(?:minimum|min\.?|at least)\s+(\d+(?:\.\d+)?)\s*{_YEAR_UNIT}"
    rf"|(\d+(?:\.\d+)?)\s*{_YEAR_UNIT}\s*(?:or more)",
    re.IGNORECASE,
)
_SINGLE_PATTERN = re.compile(
    rf"(\d+(?:\.\d+)?)\s*{_YEAR_UNIT}", re.IGNORECASE
)

# A single value/range/plus-figure shape, reused below to isolate JUST
# the text window associated with "overall"/"total" -- e.g. "10-12
# years", "10+", "10 years". The unit is optional here (matches
# _RANGE_PATTERN/_PLUS_PATTERN's own tolerance); the isolated window is
# then re-run through _parse_core() below, exactly like top-level text.
_VALUE_SHAPE = rf"\d+(?:\.\d+)?(?:\s*(?:{_DASH}|\bto\b)\s*\d+(?:\.\d+)?)?\+?\s*{_YEAR_UNIT}?"

# "10 years overall" / "overall 10 years" / "10-12 years overall" /
# "overall 10-12 years" / "10+ years overall" / "total of 10 years" /
# "overall experience of minimum 10 years": when a JD states both an
# overall/total figure and a separate "relevant" figure (e.g. "10
# years overall experience, 5 years relevant experience", or "5-8
# years relevant, 10+ years overall"), the overall/total figure is
# authoritative regardless of which one appears first in the text, and
# regardless of whether it's a single value, a range, or a "+" floor
# -- never silently substitute the "relevant" sub-figure for total
# experience. Each pattern only needs to find its own value+anchor
# substring, not match the whole sentence, so an unrelated "relevant"
# clause elsewhere in the text is never pulled in.
# The optional "minimum"/"at least" qualifier is captured INSIDE the
# group (not consumed-and-discarded outside it) so _parse_core() can
# still see it and return an open-ended floor rather than a fixed
# single value -- e.g. "overall experience of minimum 10 years" must
# stay (10, None), not collapse to (10, 10).
_MIN_QUALIFIER = r"(?:(?:minimum|min\.?|at least)\s+)?"
_VALUE_BEFORE_OVERALL_PATTERN = re.compile(
    rf"({_MIN_QUALIFIER}{_VALUE_SHAPE})\s*(?:experience\s+)?(?:of\s+)?(?:overall|total)",
    re.IGNORECASE,
)
_VALUE_AFTER_OVERALL_PATTERN = re.compile(
    rf"(?:overall|total)(?:\s+experience)?(?:\s+of)?\s*[:\-]?\s*({_MIN_QUALIFIER}{_VALUE_SHAPE})",
    re.IGNORECASE,
)
# "minimum overall experience of 10 years" / "at least overall 10 years":
# the qualifier sits BEFORE "overall"/"total" itself, not immediately
# before the number, so it falls outside _VALUE_AFTER_OVERALL_PATTERN's
# capture group entirely and was previously silently dropped, collapsing
# an open-ended floor to a fixed single value ((10, 10) instead of the
# correct (10, None)). This pattern targets exactly that qualifier
# placement; the captured value window has "minimum " re-attached by
# _extract_overall_or_total_window so _parse_core's
# _OPEN_ENDED_MIN_PATTERN still recognizes it as a floor.
_QUALIFIER_BEFORE_OVERALL_PATTERN = re.compile(
    rf"(?:minimum|min\.?|at least)\s+(?:overall|total)(?:\s+experience)?(?:\s+of)?\s*[:\-]?\s*({_VALUE_SHAPE})",
    re.IGNORECASE,
)


def _parse_core(text):
    """Range -> open-ended-minimum-phrase -> plus -> single, in that
    priority order -- the actual value-extraction logic, usable both
    on the full experience_required text and on an isolated
    overall/total window (see _extract_overall_or_total_window)."""
    range_match = _RANGE_PATTERN.search(text)
    if range_match:
        low = float(range_match.group(1))
        high = float(range_match.group(2))
        return _to_number(min(low, high)), _to_number(max(low, high))

    open_ended_match = _OPEN_ENDED_MIN_PATTERN.search(text)
    if open_ended_match:
        value = _to_number(float(open_ended_match.group(1) or open_ended_match.group(2)))
        return value, None

    plus_match = _PLUS_PATTERN.search(text)
    if plus_match:
        value = _to_number(float(plus_match.group(1)))
        return value, None

    single_match = _SINGLE_PATTERN.search(text)
    if single_match:
        value = _to_number(float(single_match.group(1)))
        return value, value

    return None, None


def _extract_overall_or_total_window(text):
    """The raw value+unit text tied to an explicit 'overall'/'total'
    qualifier (either order), or None if neither appears. Tried in
    both directions since real JDs use both ("10 years overall" and
    "overall 10 years")."""
    before_match = _VALUE_BEFORE_OVERALL_PATTERN.search(text)
    if before_match:
        return before_match.group(1)
    qualifier_before_match = _QUALIFIER_BEFORE_OVERALL_PATTERN.search(text)
    if qualifier_before_match:
        return f"minimum {qualifier_before_match.group(1)}"
    after_match = _VALUE_AFTER_OVERALL_PATTERN.search(text)
    if after_match:
        return after_match.group(1)
    return None

_PREFERRED_PATTERN = re.compile(
    r"\b(?:preferred|nice to have|good to have|desirable)\b", re.IGNORECASE
)
_REQUIRED_PATTERN = re.compile(
    r"\b(?:required|mandatory|must have|essential)\b", re.IGNORECASE
)


def detect_requirement_type(text):
    """
    REQUIRED / PREFERRED / UNKNOWN, purely for explainability -- this
    never changes the eligibility decision itself (see
    assess_experience_eligibility's docstring). Most JDs state a bare
    experience range with no qualifier at all; that stays UNKNOWN
    rather than being guessed as REQUIRED.
    """
    text = str(text or "")
    if _PREFERRED_PATTERN.search(text):
        return "PREFERRED"
    if _REQUIRED_PATTERN.search(text):
        return "REQUIRED"
    return "UNKNOWN"

# A visible-text minimum and a structured JSON-LD minimum disagreeing by
# more than this many years is treated as untrustworthy data rather than
# guessed at.
_SUSPICIOUS_DISAGREEMENT_YEARS = 2


def _to_number(value):
    """int when the value is a whole number, else the float itself."""
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def parse_experience_text(text):
    """
    Parse a human-facing experience phrase into (min_years, max_years).

    Supported (per the candidate-eligibility spec):
      "8-10 years", "8 - 10 years", "8–10 years", "8 to 10 years",
      "10-12" (bare, no unit)      -> (8, 10) / (10, 12)
      "10+ years", "10+", "10+ Yrs" -> (10, None)
      "5 years", "5 yrs", "5 yr"   -> (5, 5)
      "minimum 10 years", "at least 10 years", "10 years or more"
                                    -> (10, None) -- open-ended floor,
                                       not a fixed single value
      "10 years overall experience, 5 years relevant experience"
      "5 years relevant experience, 10 years overall required"
      "overall 10-12 years, relevant 5+ years"
      "10+ years overall, 5+ years relevant"
      "8-10 years total, 5 years relevant"
      "minimum overall experience of 10 years", "at least overall 10 years"
                                    -> (10, None) -- qualifier stated
                                       before "overall"/"total" itself
                                       still yields an open-ended floor
                                    -> the overall/total figure wins,
                                       as a single value/range/floor
                                       exactly as stated, regardless
                                       of which appears first or what
                                       shape the relevant figure is
      missing/unparseable          -> (None, None)
    """
    text = str(text or "").strip()

    if not text:
        return None, None

    overall_window = _extract_overall_or_total_window(text)
    if overall_window is not None:
        result = _parse_core(overall_window)
        if result != (None, None):
            return result
        # The overall/total anchor matched but its own window somehow
        # contained no parseable number -- fall through to whole-text
        # parsing below rather than silently returning nothing.

    return _parse_core(text)


def _format_range(min_years, max_years):
    if min_years is None and max_years is None:
        return "unspecified"
    if max_years is None:
        return f"{min_years}+ years"
    if min_years == max_years:
        return f"{min_years} years"
    return f"{min_years}-{max_years} years"


def assess_experience_eligibility(job, candidate_profile):
    """
    Assess whether `job`'s required experience is compatible with
    `candidate_profile`'s experience_years.

    `job` is expected to be a normalized job dict (discover_local
    .normalize_job() output, or a Naukri RawJob dict) carrying
    "experience_required" and, optionally, "experience_min_months".

    `candidate_profile` is a candidate profile dict shaped like
    config/profile.json (candidate_profile["candidate"]
    ["experience_years"]) -- supplied explicitly by the caller, the same
    convention score_job() uses. There is no default/global fallback
    here either.

    Never returns eligibility=None and never silently drops UNKNOWN --
    every input, however unparseable or contradictory, produces an
    ExperienceAssessment with an explicit eligibility state and a
    human-readable reason.

    Boundary-case note: a candidate whose years exactly equal a range's
    min or max edge (e.g. "11-15 years" vs. 11 years, or "11 years" vs.
    11 years) is classified MATCH, not BORDERLINE. BORDERLINE is defined
    in the Eligibility enum but intentionally unused by this
    implementation -- no exact-boundary case in the spec/tests requires
    distinguishing it from MATCH, and treating an in-range edge as an
    ordinary MATCH is the simpler, unambiguous choice. It remains
    available for a future caller that needs a narrower distinction.
    """
    candidate_years = candidate_profile["candidate"]["experience_years"]

    experience_text = job.get("experience_required", "")
    source_min_months = job.get("experience_min_months")

    visible_min, visible_max = parse_experience_text(experience_text)
    requirement_type = detect_requirement_type(experience_text)

    structured_min_years = None
    if source_min_months is not None:
        structured_min_years = _to_number(source_min_months / 12.0)

    # Suspicious-data cross-check: only meaningful when both an
    # independently-parsed visible minimum and a structured minimum are
    # present. If they materially disagree, refuse to guess which is
    # right rather than silently trusting one of them.
    if visible_min is not None and structured_min_years is not None:
        if abs(visible_min - structured_min_years) > _SUSPICIOUS_DISAGREEMENT_YEARS:
            return ExperienceAssessment(
                eligibility=Eligibility.UNKNOWN,
                parsed_min_years=visible_min,
                parsed_max_years=visible_max,
                candidate_experience_years=candidate_years,
                source_min_experience_months=source_min_months,
                requirement_type=requirement_type,
                is_suspicious=True,
                reason=(
                    f"Visible-text minimum experience ({visible_min} years) "
                    f"and structured minimum experience "
                    f"({structured_min_years} years, from "
                    f"{source_min_months} months) disagree by more than "
                    f"{_SUSPICIOUS_DISAGREEMENT_YEARS} years; treating as "
                    f"unreliable rather than guessing which is correct."
                ),
            )

    # Structured data is preferred for the minimum when available (it
    # either agrees with the visible minimum, per the check above, or
    # the visible minimum is absent). The maximum can only ever come
    # from visible text -- Naukri's JSON-LD exposes no structured
    # ceiling.
    effective_min = (
        structured_min_years if structured_min_years is not None else visible_min
    )
    effective_max = visible_max

    if effective_min is None and effective_max is None:
        return ExperienceAssessment(
            eligibility=Eligibility.UNKNOWN,
            parsed_min_years=visible_min,
            parsed_max_years=visible_max,
            candidate_experience_years=candidate_years,
            source_min_experience_months=source_min_months,
            requirement_type=requirement_type,
            is_suspicious=False,
            reason="Missing or unparseable experience requirement.",
        )

    range_desc = _format_range(effective_min, effective_max)

    if effective_max is not None and candidate_years > effective_max:
        return ExperienceAssessment(
            eligibility=Eligibility.BELOW_PROFILE,
            parsed_min_years=visible_min,
            parsed_max_years=visible_max,
            candidate_experience_years=candidate_years,
            source_min_experience_months=source_min_months,
            requirement_type=requirement_type,
            is_suspicious=False,
            reason=(
                f"Excluded: required experience {range_desc}, "
                f"candidate has {candidate_years} years."
            ),
        )

    if effective_min is not None and candidate_years < effective_min:
        return ExperienceAssessment(
            eligibility=Eligibility.ABOVE_PROFILE,
            parsed_min_years=visible_min,
            parsed_max_years=visible_max,
            candidate_experience_years=candidate_years,
            source_min_experience_months=source_min_months,
            requirement_type=requirement_type,
            is_suspicious=False,
            reason=(
                f"Required experience {range_desc} exceeds candidate's "
                f"{candidate_years} years."
            ),
        )

    return ExperienceAssessment(
        eligibility=Eligibility.MATCH,
        parsed_min_years=visible_min,
        parsed_max_years=visible_max,
        candidate_experience_years=candidate_years,
        source_min_experience_months=source_min_months,
        requirement_type=requirement_type,
        is_suspicious=False,
        reason=(
            f"Candidate's {candidate_years} years fits required "
            f"experience {range_desc}."
        ),
    )
