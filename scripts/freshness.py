#!/usr/bin/env python3

"""
Deterministic job-posting freshness classification.

Independent of, and never used to alter, the existing 100-point match
score -- freshness is a separate attribute for future ranking/
application-prioritization use, derived purely from a job's own
posted_date text.

No source adapter in this project currently returns a single consistent
date format: Naukri's own posted_date is a relative human phrase
("Posted: Few hours ago"), while locally-entered/mock jobs use an ISO
"YYYY-MM-DD" string. Both are handled here; anything else -- missing,
empty, or genuinely unparseable text -- is UNKNOWN, never guessed at.

The reference date ("today") is never hardcoded: classify_freshness()
defaults it to the real wall-clock date (datetime.now(timezone.utc))
only when the caller does not supply one. Callers that need
reproducible results (all of this module's own tests) always pass an
explicit reference_date instead of relying on the real current date.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum


class FreshnessCategory(Enum):
    HOT = "HOT"
    FRESH = "FRESH"
    AGING = "AGING"
    OLD = "OLD"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


@dataclass
class FreshnessAssessment:
    category: FreshnessCategory
    age_days: int | None
    parsed_date: str | None
    reason: str


_ISO_DATE_PATTERN = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

_DAYS_AGO_PATTERN = re.compile(r"(\d+)\+?\s*days?\s*ago", re.IGNORECASE)
_WEEKS_AGO_PATTERN = re.compile(r"(\d+)\+?\s*weeks?\s*ago", re.IGNORECASE)
_MONTHS_AGO_PATTERN = re.compile(r"(\d+)\+?\s*months?\s*ago", re.IGNORECASE)
_HOURS_AGO_OR_TODAY_PATTERN = re.compile(
    r"\b(just now|today|few hours? ago|\d+\s*hours?\s*ago)\b", re.IGNORECASE
)
_YESTERDAY_PATTERN = re.compile(r"\byesterday\b", re.IGNORECASE)


def _parse_age_days(text, reference_date):
    """
    Returns (age_days, parsed_date_iso) or (None, None) if the text is
    empty or not recognized by any supported format. age_days may be
    negative for a date after reference_date (a future date) -- the
    caller (classify_freshness) decides how to treat that, this
    function only reports what it parsed.
    """
    text = str(text or "").strip()
    if not text:
        return None, None

    iso_match = _ISO_DATE_PATTERN.search(text)
    if iso_match:
        year, month, day = (int(part) for part in iso_match.groups())
        try:
            parsed = date(year, month, day)
        except ValueError:
            return None, None
        return (reference_date - parsed).days, parsed.isoformat()

    lower = text.lower()

    if _YESTERDAY_PATTERN.search(lower):
        return 1, (reference_date - timedelta(days=1)).isoformat()

    if _HOURS_AGO_OR_TODAY_PATTERN.search(lower):
        return 0, reference_date.isoformat()

    months_match = _MONTHS_AGO_PATTERN.search(lower)
    if months_match:
        age = int(months_match.group(1)) * 30
        return age, (reference_date - timedelta(days=age)).isoformat()

    weeks_match = _WEEKS_AGO_PATTERN.search(lower)
    if weeks_match:
        age = int(weeks_match.group(1)) * 7
        return age, (reference_date - timedelta(days=age)).isoformat()

    days_match = _DAYS_AGO_PATTERN.search(lower)
    if days_match:
        age = int(days_match.group(1))
        return age, (reference_date - timedelta(days=age)).isoformat()

    return None, None


def classify_freshness(posted_date_text, reference_date=None):
    """
    Classify a job's freshness from its raw posted_date text.

    reference_date: a date or datetime to treat as "today". Defaults to
    the real current UTC date when omitted -- never a hardcoded literal
    date in this function's own logic.
    """
    if reference_date is None:
        reference_date = datetime.now(timezone.utc).date()
    elif isinstance(reference_date, datetime):
        reference_date = reference_date.date()

    age_days, parsed_date = _parse_age_days(posted_date_text, reference_date)

    if age_days is None:
        return FreshnessAssessment(
            category=FreshnessCategory.UNKNOWN,
            age_days=None,
            parsed_date=None,
            reason="posted-date text is missing or not in a recognized format",
        )

    if age_days < 0:
        # A posted date after the reference date is nonsensical for a
        # real job posting. Handled conservatively: never classified as
        # HOT/FRESH just because the arithmetic came out negative.
        return FreshnessAssessment(
            category=FreshnessCategory.UNKNOWN,
            age_days=age_days,
            parsed_date=parsed_date,
            reason=(
                f"posted date ({parsed_date}) is {-age_days} day(s) after "
                "the reference date -- treated conservatively as unknown "
                "freshness rather than guessed"
            ),
        )

    if age_days <= 2:
        category = FreshnessCategory.HOT
    elif age_days <= 7:
        category = FreshnessCategory.FRESH
    elif age_days <= 14:
        category = FreshnessCategory.AGING
    elif age_days <= 30:
        category = FreshnessCategory.OLD
    else:
        category = FreshnessCategory.STALE

    return FreshnessAssessment(
        category=category,
        age_days=age_days,
        parsed_date=parsed_date,
        reason=f"{age_days} day(s) since posting",
    )
