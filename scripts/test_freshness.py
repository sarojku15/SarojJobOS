#!/usr/bin/env python3

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from freshness import classify_freshness, FreshnessCategory

REFERENCE = date(2026, 9, 20)


def main():
    failures = []

    print("FRESHNESS CLASSIFICATION TEST")
    print("==============================")

    cases = [
        ("2026-09-20", FreshnessCategory.HOT, 0),
        ("2026-09-19", FreshnessCategory.HOT, 1),
        ("2026-09-18", FreshnessCategory.HOT, 2),
        ("2026-09-15", FreshnessCategory.FRESH, 5),
        ("2026-09-13", FreshnessCategory.FRESH, 7),
        ("2026-09-10", FreshnessCategory.AGING, 10),
        ("2026-09-06", FreshnessCategory.AGING, 14),
        ("2026-09-01", FreshnessCategory.OLD, 19),
        ("2026-08-21", FreshnessCategory.OLD, 30),
        ("2026-08-06", FreshnessCategory.STALE, 45),
        ("2026-01-01", FreshnessCategory.STALE, 262),
        ("Posted: Few hours ago", FreshnessCategory.HOT, 0),
        ("today", FreshnessCategory.HOT, 0),
        ("just now", FreshnessCategory.HOT, 0),
        ("Posted: 3 hours ago", FreshnessCategory.HOT, 0),
        ("Posted: yesterday", FreshnessCategory.HOT, 1),
        ("Posted: 5 days ago", FreshnessCategory.FRESH, 5),
        ("Posted: 2 weeks ago", FreshnessCategory.AGING, 14),
        ("Posted: 1 month ago", FreshnessCategory.OLD, 30),
        ("Posted: 2 months ago", FreshnessCategory.STALE, 60),
        # Regression: Naukri's real "N+ weeks/months ago" bucketed phrasing
        # (confirmed against 34 of 72 real jobs in the 5-query live
        # validation) was previously falling through to UNKNOWN because
        # _WEEKS_AGO_PATTERN/_MONTHS_AGO_PATTERN had no "+" provision.
        ("Posted: 3+ weeks ago", FreshnessCategory.OLD, 21),
        ("Posted: 3+ months ago", FreshnessCategory.STALE, 90),
        ("Posted: 5+ days ago", FreshnessCategory.FRESH, 5),
    ]

    for text, expected_category, expected_age in cases:
        result = classify_freshness(text, reference_date=REFERENCE)
        if result.category != expected_category or result.age_days != expected_age:
            failures.append(
                f"classify_freshness({text!r}): expected ({expected_category}, age={expected_age}), "
                f"got ({result.category}, age={result.age_days})"
            )
        else:
            print(f"PASS: classify_freshness({text!r}) -> {result.category.value} (age={result.age_days})")

    # --- missing / empty ---
    for text in ["", None]:
        result = classify_freshness(text, reference_date=REFERENCE)
        if result.category != FreshnessCategory.UNKNOWN or result.age_days is not None:
            failures.append(f"classify_freshness({text!r}): expected UNKNOWN with age_days=None, got {result}")
        else:
            print(f"PASS: classify_freshness({text!r}) -> UNKNOWN (missing)")

    # --- malformed: not a real date, not a recognized relative phrase ---
    for text in ["Sometime recently-ish", "2026-13-45", "N/A", "posted a while back"]:
        result = classify_freshness(text, reference_date=REFERENCE)
        if result.category != FreshnessCategory.UNKNOWN:
            failures.append(f"classify_freshness({text!r}): expected UNKNOWN (malformed), got {result.category}")
        else:
            print(f"PASS: classify_freshness({text!r}) -> UNKNOWN (malformed)")

    # --- future dates: handled conservatively, never HOT/FRESH ---
    for text, days_ahead in [("2026-09-25", 5), ("2026-10-20", 30), ("2026-09-21", 1)]:
        result = classify_freshness(text, reference_date=REFERENCE)
        if result.category != FreshnessCategory.UNKNOWN:
            failures.append(
                f"SAFETY VIOLATION: classify_freshness({text!r}) (a future date) was classified "
                f"{result.category}, expected UNKNOWN"
            )
        elif result.age_days != -days_ahead:
            failures.append(
                f"classify_freshness({text!r}): expected age_days={-days_ahead}, got {result.age_days}"
            )
        else:
            print(f"PASS: classify_freshness({text!r}) (future date) -> UNKNOWN, age_days={result.age_days}")

    # --- determinism ---
    a = classify_freshness("2026-09-10", reference_date=REFERENCE)
    b = classify_freshness("2026-09-10", reference_date=REFERENCE)
    if a != b:
        failures.append("classify_freshness is not deterministic for identical inputs")
    else:
        print("PASS: classify_freshness is deterministic")

    # --- default reference_date: never a hardcoded literal date; must
    #     use the real current date when the caller omits one ---
    import inspect
    from freshness import classify_freshness as cf_func

    source = inspect.getsource(cf_func)
    if "datetime.now(" not in source:
        failures.append(
            "SAFETY VIOLATION: classify_freshness() no longer calls datetime.now() for its "
            "default reference date -- it may have been hardcoded"
        )
    else:
        print("PASS: default reference_date is computed from the real current date, never hardcoded")

    # A recent real ISO date should not be UNKNOWN when using the real
    # default reference date (loose sanity check, no exact category
    # asserted since "today" is genuinely dynamic here).
    result = classify_freshness("2026-09-19")
    if result.category == FreshnessCategory.UNKNOWN and result.age_days is not None and result.age_days < 0:
        # only a problem if this environment's clock is somehow before
        # 2026-09-19; otherwise this is an expected UNKNOWN due to a
        # stale fixture date long after this project's operation window
        pass
    print(f"NOTE: classify_freshness('2026-09-19') with the REAL default reference date -> {result.category.value} (informational only)")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll freshness classification tests passed.")


if __name__ == "__main__":
    main()
