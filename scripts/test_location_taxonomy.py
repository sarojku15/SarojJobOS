#!/usr/bin/env python3

"""
Tests for scripts/location_taxonomy.py.

Fully isolated: pure in-memory computation, no DB, no network, no
dependency on config/profile.json's actual content -- candidate
profiles used here are fabricated in-memory dicts, matching the shape
of config/profile.json but standing in for arbitrary candidates (per
CLAUDE.md's "do not turn this into a Saroj-only implementation" and
this component's explicit "no candidate-specific values hardcoded into
location_taxonomy.py" requirement).
"""

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import location_taxonomy
from location_taxonomy import (
    LocationEligibility,
    LocationKind,
    WorkModel,
    assess_location_eligibility,
    normalize_location_text,
    normalize_work_model_text,
    parse_job_location_and_work_model,
    parse_locations,
)


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _job(location, work_model=""):
    return {"location": location, "work_model": work_model}


def _profile(target_locations):
    return {"target_locations": target_locations}


# --------------------------------------------------------------------
# 1-9: single-location normalization
# --------------------------------------------------------------------

def test_1_bangalore_normalizes_to_bengaluru():
    failures = []
    loc = normalize_location_text("Bangalore")
    if loc.kind != LocationKind.CITY or loc.city != "Bengaluru":
        _fail(failures, f"test 1: expected CITY Bengaluru, got kind={loc.kind} city={loc.city}")
    else:
        print("PASS: test 1 -> Bangalore normalizes to CITY Bengaluru")
    return failures


def test_2_bengaluru_normalizes_to_bengaluru():
    failures = []
    loc = normalize_location_text("Bengaluru")
    if loc.kind != LocationKind.CITY or loc.city != "Bengaluru":
        _fail(failures, f"test 2: expected CITY Bengaluru, got kind={loc.kind} city={loc.city}")
    else:
        print("PASS: test 2 -> Bengaluru normalizes to CITY Bengaluru")
    return failures


def test_3_hyderabad():
    failures = []
    loc = normalize_location_text("Hyderabad")
    if loc.kind != LocationKind.CITY or loc.city != "Hyderabad":
        _fail(failures, f"test 3: expected CITY Hyderabad, got kind={loc.kind} city={loc.city}")
    else:
        print("PASS: test 3 -> Hyderabad normalizes to CITY Hyderabad")
    return failures


def test_4_pune():
    failures = []
    loc = normalize_location_text("Pune")
    if loc.kind != LocationKind.CITY or loc.city != "Pune":
        _fail(failures, f"test 4: expected CITY Pune, got kind={loc.kind} city={loc.city}")
    else:
        print("PASS: test 4 -> Pune normalizes to CITY Pune")
    return failures


def test_5_chennai():
    failures = []
    loc = normalize_location_text("Chennai")
    if loc.kind != LocationKind.CITY or loc.city != "Chennai":
        _fail(failures, f"test 5: expected CITY Chennai, got kind={loc.kind} city={loc.city}")
    else:
        print("PASS: test 5 -> Chennai normalizes to CITY Chennai")
    return failures


def test_6_remote_india():
    failures = []
    loc = normalize_location_text("Remote India")
    if loc.kind != LocationKind.REMOTE_COUNTRY or loc.country != "India":
        _fail(failures, f"test 6: expected REMOTE_COUNTRY India, got kind={loc.kind} country={loc.country}")
    else:
        print("PASS: test 6 -> 'Remote India' normalizes to REMOTE_COUNTRY/India")
    return failures


def test_7_remote_global():
    failures = []
    loc = normalize_location_text("Remote Global")
    if loc.kind != LocationKind.REMOTE_GLOBAL:
        _fail(failures, f"test 7: expected REMOTE_GLOBAL, got kind={loc.kind}")
    else:
        print("PASS: test 7 -> 'Remote Global' normalizes to REMOTE_GLOBAL")
    return failures


def test_8_pan_india():
    failures = []
    loc_a = normalize_location_text("PAN India")
    loc_b = normalize_location_text("Pan India")
    if loc_a.kind != LocationKind.PAN_COUNTRY or loc_a.country != "India":
        _fail(failures, f"test 8: 'PAN India' expected PAN_COUNTRY India, got kind={loc_a.kind} country={loc_a.country}")
    elif loc_b.kind != LocationKind.PAN_COUNTRY or loc_b.country != "India":
        _fail(failures, f"test 8: 'Pan India' expected PAN_COUNTRY India, got kind={loc_b.kind} country={loc_b.country}")
    else:
        print("PASS: test 8 -> 'PAN India'/'Pan India' both normalize to PAN_COUNTRY/India")
    return failures


def test_9_delhi_ncr():
    failures = []
    loc_a = normalize_location_text("Delhi NCR")
    loc_b = normalize_location_text("Delhi / NCR")
    if loc_a.kind != LocationKind.REGION or loc_a.region != "Delhi NCR":
        _fail(failures, f"test 9: 'Delhi NCR' expected REGION Delhi NCR, got kind={loc_a.kind} region={loc_a.region}")
    elif loc_b.kind != LocationKind.REGION or loc_b.region != "Delhi NCR":
        _fail(failures, f"test 9: 'Delhi / NCR' expected REGION Delhi NCR (not split into two locations), got kind={loc_b.kind} region={loc_b.region}")
    else:
        print("PASS: test 9 -> 'Delhi NCR' and 'Delhi / NCR' both normalize to REGION/Delhi NCR (not split)")
    return failures


# --------------------------------------------------------------------
# 10-11: multi-location parsing
# --------------------------------------------------------------------

def test_10_multi_city_job():
    failures = []
    locs = parse_locations("Bengaluru / Hyderabad / Pune")
    cities = sorted(l.city for l in locs if l.kind == LocationKind.CITY)
    if len(locs) != 3 or cities != ["Bengaluru", "Hyderabad", "Pune"]:
        _fail(failures, f"test 10: expected 3 separate cities [Bengaluru, Hyderabad, Pune], got {len(locs)} entries: {[(l.kind, l.city) for l in locs]}")
    else:
        print(f"PASS: test 10 -> multi-city job parses into 3 distinct CITY entries: {cities}")
    return failures


def test_11_candidate_with_multiple_cities():
    failures = []
    profile = _profile(["Bengaluru", "Hyderabad", "Pune"])
    candidate_locations = [normalize_location_text(x) for x in profile["target_locations"]]
    cities = sorted(l.city for l in candidate_locations)
    if cities != ["Bengaluru", "Hyderabad", "Pune"]:
        _fail(failures, f"test 11: expected candidate to carry 3 distinct city preferences, got {cities}")
    else:
        print(f"PASS: test 11 -> candidate profile with multiple cities normalizes to {cities}")
    return failures


# --------------------------------------------------------------------
# 12-19: eligibility examples from the spec
# --------------------------------------------------------------------

def test_12_candidate_bangalore_job_bengaluru():
    failures = []
    result = assess_location_eligibility(_job("Bengaluru"), _profile(["Bangalore"]))
    if result.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"test 12: expected MATCH, got {result.eligibility}")
    else:
        print("PASS: test 12 -> candidate 'Bangalore' + job 'Bengaluru' -> MATCH")
    return failures


def test_13_candidate_bangalore_job_hyderabad():
    failures = []
    result = assess_location_eligibility(_job("Hyderabad"), _profile(["Bangalore"]))
    if result.eligibility != LocationEligibility.NO_MATCH:
        _fail(failures, f"test 13: expected NO_MATCH, got {result.eligibility}")
    else:
        print("PASS: test 13 -> candidate 'Bangalore' + job 'Hyderabad' -> NO_MATCH")
    return failures


def test_14_candidate_beng_hyd_job_hyderabad():
    failures = []
    result = assess_location_eligibility(_job("Hyderabad"), _profile(["Bengaluru", "Hyderabad"]))
    if result.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"test 14: expected MATCH, got {result.eligibility}")
    else:
        print("PASS: test 14 -> candidate Bengaluru/Hyderabad + job Hyderabad -> MATCH")
    return failures


def test_15_candidate_bengaluru_pan_india():
    failures = []
    result = assess_location_eligibility(_job("PAN India"), _profile(["Bengaluru"]))
    if result.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"test 15: expected MATCH, got {result.eligibility}")
    else:
        print("PASS: test 15 -> candidate Bengaluru + job PAN India -> MATCH")
    return failures


def test_16_candidate_bengaluru_remote_india_no_remote_pref():
    failures = []
    result = assess_location_eligibility(_job("Remote India"), _profile(["Bengaluru"]))
    if result.eligibility != LocationEligibility.NO_MATCH:
        _fail(failures, f"test 16: expected NO_MATCH (candidate never accepted remote), got {result.eligibility}")
    else:
        print("PASS: test 16 -> candidate Bengaluru (no remote pref) + job Remote India -> NO_MATCH")
    return failures


def test_17_candidate_bengaluru_remote_india_with_remote_pref():
    failures = []
    result = assess_location_eligibility(_job("Remote India"), _profile(["Bengaluru", "Remote India"]))
    if result.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"test 17: expected MATCH, got {result.eligibility}")
    else:
        print("PASS: test 17 -> candidate Bengaluru + Remote India + job Remote India -> MATCH")
    return failures


def test_18_candidate_bengaluru_remote_global_without_global_pref():
    failures = []
    result = assess_location_eligibility(_job("Remote Global"), _profile(["Bengaluru"]))
    if result.eligibility != LocationEligibility.NO_MATCH:
        _fail(failures, f"test 18: expected NO_MATCH (candidate never accepted global remote), got {result.eligibility}")
    else:
        print("PASS: test 18 -> candidate Bengaluru (no global pref) + job Remote Global -> NO_MATCH")
    return failures


def test_19_candidate_with_global_remote_pref():
    failures = []
    result = assess_location_eligibility(_job("Remote Global"), _profile(["Remote Global"]))
    if result.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"test 19: expected MATCH, got {result.eligibility}")
    else:
        print("PASS: test 19 -> candidate with global remote pref + job Remote Global -> MATCH")
    return failures


# --------------------------------------------------------------------
# 20-21: unknown locations
# --------------------------------------------------------------------

def test_20_unknown_job_location():
    failures = []
    result = assess_location_eligibility(_job(""), _profile(["Bengaluru"]))
    if result.eligibility != LocationEligibility.UNKNOWN:
        _fail(failures, f"test 20: expected UNKNOWN for missing job location, got {result.eligibility}")
    else:
        print("PASS: test 20 -> missing/unparseable job location -> UNKNOWN (not silently rejected)")
    return failures


def test_21_unknown_candidate_location():
    failures = []
    result = assess_location_eligibility(_job("Bengaluru"), _profile(["Atlantis"]))
    if result.eligibility != LocationEligibility.UNKNOWN:
        _fail(failures, f"test 21: expected UNKNOWN for an unparseable candidate location, got {result.eligibility}")
    else:
        print("PASS: test 21 -> unparseable candidate target location -> UNKNOWN (not falsely rejected)")
    return failures


# --------------------------------------------------------------------
# 22-25: work model
# --------------------------------------------------------------------

def test_22_hybrid_work_model():
    failures = []
    locs, wm = parse_job_location_and_work_model("Bengaluru - Hybrid")
    if wm != WorkModel.HYBRID:
        _fail(failures, f"test 22: expected HYBRID, got {wm}")
    elif len(locs) != 1 or locs[0].kind != LocationKind.CITY or locs[0].city != "Bengaluru":
        _fail(failures, f"test 22: expected location=CITY Bengaluru, got {[(l.kind, l.city) for l in locs]}")
    else:
        print("PASS: test 22 -> 'Bengaluru - Hybrid' -> location=Bengaluru, work_model=HYBRID")
    return failures


def test_23_onsite_work_model():
    failures = []
    locs, wm = parse_job_location_and_work_model("Bengaluru - Work from office")
    if wm != WorkModel.ONSITE:
        _fail(failures, f"test 23: expected ONSITE, got {wm}")
    elif len(locs) != 1 or locs[0].city != "Bengaluru":
        _fail(failures, f"test 23: expected location=CITY Bengaluru, got {[(l.kind, l.city) for l in locs]}")
    else:
        print("PASS: test 23 -> 'Bengaluru - Work from office' -> location=Bengaluru, work_model=ONSITE")
    return failures


def test_24_remote_work_model():
    failures = []
    locs, wm = parse_job_location_and_work_model("Remote - India")
    if wm != WorkModel.REMOTE:
        _fail(failures, f"test 24: expected REMOTE, got {wm}")
    elif len(locs) != 1 or locs[0].kind != LocationKind.REMOTE_COUNTRY or locs[0].country != "India":
        _fail(failures, f"test 24: expected location=REMOTE_COUNTRY India, got {[(l.kind, l.country) for l in locs]}")
    else:
        print("PASS: test 24 -> 'Remote - India' -> location=REMOTE_COUNTRY/India, work_model=REMOTE")
    return failures


def test_25_work_model_unknown():
    failures = []
    locs, wm = parse_job_location_and_work_model("Bengaluru")
    if wm != WorkModel.UNKNOWN:
        _fail(failures, f"test 25: expected UNKNOWN work model for a bare city with no modifier, got {wm}")
    else:
        print("PASS: test 25 -> bare 'Bengaluru' (no modifier) -> work_model=UNKNOWN (not inferred)")
    return failures


# --------------------------------------------------------------------
# 26-27: multi-location + work model combined
# --------------------------------------------------------------------

def test_26_multi_location_plus_hybrid():
    failures = []
    locs, wm = parse_job_location_and_work_model("Bengaluru", "Hybrid")
    if wm != WorkModel.HYBRID:
        _fail(failures, f"test 26: expected HYBRID from the separate work_model field, got {wm}")
    elif len(locs) != 1 or locs[0].city != "Bengaluru":
        _fail(failures, f"test 26: expected CITY Bengaluru, got {[(l.kind, l.city) for l in locs]}")
    else:
        print("PASS: test 26 -> location field 'Bengaluru' + separate work_model field 'Hybrid' -> HYBRID, location unaffected")
    return failures


def test_27_multi_location_plus_remote():
    failures = []
    locs, wm = parse_job_location_and_work_model("Bengaluru / Hyderabad / Pune", "Remote")
    cities = sorted(l.city for l in locs if l.kind == LocationKind.CITY)
    if wm != WorkModel.REMOTE:
        _fail(failures, f"test 27: expected REMOTE from the separate work_model field, got {wm}")
    elif cities != ["Bengaluru", "Hyderabad", "Pune"]:
        _fail(failures, f"test 27: expected all 3 cities preserved (not collapsed) alongside work_model=REMOTE, got {cities}")
    else:
        print(f"PASS: test 27 -> multi-city location {cities} + separate work_model field 'Remote' -> REMOTE, all cities preserved")
    return failures


# --------------------------------------------------------------------
# 28: different candidates, same job
# --------------------------------------------------------------------

def test_28_different_candidates_different_eligibility():
    failures = []
    job = _job("Hyderabad")

    result_bengaluru_only = assess_location_eligibility(job, _profile(["Bengaluru"]))
    result_hyderabad_pref = assess_location_eligibility(job, _profile(["Hyderabad"]))

    if result_bengaluru_only.eligibility == result_hyderabad_pref.eligibility:
        _fail(
            failures,
            f"test 28: expected different eligibility for a materially "
            f"different candidate on the same job, both got "
            f"{result_bengaluru_only.eligibility}",
        )
    elif result_bengaluru_only.eligibility != LocationEligibility.NO_MATCH:
        _fail(failures, f"test 28: expected Bengaluru-only candidate NO_MATCH for a Hyderabad job, got {result_bengaluru_only.eligibility}")
    elif result_hyderabad_pref.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"test 28: expected Hyderabad-preferring candidate MATCH for a Hyderabad job, got {result_hyderabad_pref.eligibility}")
    else:
        print(
            f"PASS: test 28 -> Bengaluru-only candidate -> "
            f"{result_bengaluru_only.eligibility.value}, Hyderabad-preferring "
            f"candidate -> {result_hyderabad_pref.eligibility.value}"
        )
    return failures


# --------------------------------------------------------------------
# 29-32: additional required checks
# --------------------------------------------------------------------

def test_29_bangalore_bengaluru_normalization_equivalence():
    failures = []
    a = normalize_location_text("Bangalore")
    b = normalize_location_text("Bengaluru")
    if (a.kind, a.city, a.country) != (b.kind, b.city, b.country):
        _fail(
            failures,
            f"test 29: expected 'Bangalore' and 'Bengaluru' to normalize "
            f"identically, got {(a.kind, a.city, a.country)} vs "
            f"{(b.kind, b.city, b.country)}",
        )
    else:
        print("PASS: test 29 -> 'Bangalore' and 'Bengaluru' normalize identically")
    return failures


def test_30_multi_location_preservation():
    failures = []
    locs = parse_locations("Bengaluru, Hyderabad, Chennai")
    cities = [l.city for l in locs]
    if cities != ["Bengaluru", "Hyderabad", "Chennai"]:
        _fail(
            failures,
            f"test 30: expected 3 distinct cities in order "
            f"[Bengaluru, Hyderabad, Chennai] (not collapsed into one), "
            f"got {cities}",
        )
    else:
        print(f"PASS: test 30 -> multi-location string preserved as {len(cities)} distinct entries: {cities}")
    return failures


def test_31_unknown_preservation():
    failures = []
    loc = normalize_location_text("Nowhereland")
    if loc.kind != LocationKind.UNKNOWN:
        _fail(failures, f"test 31: expected an unrecognized location to stay UNKNOWN, got {loc.kind}")
        return failures

    result = assess_location_eligibility(_job("Nowhereland"), _profile(["Bengaluru"]))
    if result.eligibility != LocationEligibility.UNKNOWN:
        _fail(
            failures,
            f"test 31: expected an unrecognized job location to produce "
            f"eligibility UNKNOWN end-to-end (never silently rejected as "
            f"NO_MATCH), got {result.eligibility}",
        )
    else:
        print("PASS: test 31 -> unrecognized location stays UNKNOWN through both normalize_location_text() and assess_location_eligibility()")
    return failures


def test_32_no_candidate_specific_hardcoding():
    """
    Checks the real guarantees behind "no candidate-specific values are
    hardcoded into location_taxonomy.py":

      (a) the module never reads a config file at runtime (no `open(`,
          no `json.load`, no `import json`) -- candidate data can only
          ever arrive as a function argument, never loaded internally;
      (b) assess_location_eligibility() requires an explicit
          candidate_profile parameter with NO default value, so it
          cannot silently fall back to any particular candidate; and
      (c) no literal candidate name ("Saroj", "Nayak") appears anywhere
          in the module as an actual token.

    Deliberately does NOT flag the word "profile.json" or "Saroj"
    appearing inside a comment/docstring explaining that the module has
    no such dependency -- that prose is the documentation of this exact
    guarantee, not a violation of it. A naive whole-source substring
    match over comments produces false positives on that documentation;
    this checks the functional guarantees directly instead.
    """
    failures = []

    source = inspect.getsource(location_taxonomy)

    if "import json" in source or "json.load" in source or "open(" in source:
        _fail(
            failures,
            "test 32: location_taxonomy.py appears to read a file at "
            "runtime (found 'import json'/'json.load'/'open(') -- "
            "candidate data must only ever arrive as a function argument",
        )

    signature = inspect.signature(assess_location_eligibility)
    candidate_param = signature.parameters.get("candidate_profile")

    if candidate_param is None:
        _fail(
            failures,
            "test 32: assess_location_eligibility() has no "
            "candidate_profile parameter at all",
        )
    elif candidate_param.default is not inspect.Parameter.empty:
        _fail(
            failures,
            f"test 32: assess_location_eligibility()'s candidate_profile "
            f"has a default value ({candidate_param.default!r}) -- it "
            f"must be required so no candidate can be silently assumed",
        )

    name_tokens_found = [
        token for token in ("Saroj", "Nayak")
        if token in source
    ]

    if name_tokens_found:
        _fail(
            failures,
            f"test 32: location_taxonomy.py contains a literal candidate "
            f"name token: {name_tokens_found}",
        )

    if not failures:
        print(
            "PASS: test 32 -> location_taxonomy.py never reads a config "
            "file, requires an explicit candidate_profile argument with "
            "no default, and contains no literal candidate name"
        )

    return failures


# --------------------------------------------------------------------
# 33-42: remote-scope refinement -- a generic "Remote" preference must
# never be silently treated as an implicit "Remote Global" acceptance.
# --------------------------------------------------------------------

def test_33_generic_remote_plus_remote_india_job():
    failures = []
    result = assess_location_eligibility(_job("Remote India"), _profile(["Remote"]))
    if result.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"test 33: expected MATCH, got {result.eligibility}")
    else:
        print("PASS: test 33 -> candidate 'Remote' + job 'Remote India' -> MATCH")
    return failures


def test_34_generic_remote_plus_remote_global_job():
    """
    The core fix: a generic, unscoped "Remote" candidate preference must
    NOT be treated as an implicit global-remote acceptance. Chosen
    deterministic behavior: NO_MATCH (see location_taxonomy.py's
    _remote_pair_eligibility() docstring for why NO_MATCH rather than
    UNKNOWN was chosen).
    """
    failures = []
    result = assess_location_eligibility(_job("Remote Global"), _profile(["Remote"]))
    if result.eligibility != LocationEligibility.NO_MATCH:
        _fail(
            failures,
            f"test 34: expected NO_MATCH (a generic 'Remote' preference "
            f"must not automatically authorize a global remote job), "
            f"got {result.eligibility}",
        )
    else:
        print("PASS: test 34 -> candidate 'Remote' (generic) + job 'Remote Global' -> NO_MATCH (not auto-authorized)")
    return failures


def test_35_remote_india_plus_remote_india_job():
    failures = []
    result = assess_location_eligibility(_job("Remote India"), _profile(["Remote India"]))
    if result.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"test 35: expected MATCH, got {result.eligibility}")
    else:
        print("PASS: test 35 -> candidate 'Remote India' + job 'Remote India' -> MATCH")
    return failures


def test_36_remote_india_plus_remote_global_job():
    """
    A country-scoped remote preference must not automatically MATCH a
    global remote job just because global scope technically includes
    that country -- chosen deterministic behavior: UNKNOWN (the
    candidate did express some remote acceptance, so a flat NO_MATCH
    would be too strict; but explicit global acceptance was never
    given, so MATCH would be a guess).
    """
    failures = []
    result = assess_location_eligibility(_job("Remote Global"), _profile(["Remote India"]))
    if result.eligibility != LocationEligibility.UNKNOWN:
        _fail(
            failures,
            f"test 36: expected UNKNOWN (do not automatically MATCH a "
            f"country-scoped remote preference against a global job), "
            f"got {result.eligibility}",
        )
    else:
        print("PASS: test 36 -> candidate 'Remote India' + job 'Remote Global' -> UNKNOWN (not auto-matched)")
    return failures


def test_37_remote_global_plus_remote_global_job():
    failures = []
    result = assess_location_eligibility(_job("Remote Global"), _profile(["Remote Global"]))
    if result.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"test 37: expected MATCH, got {result.eligibility}")
    else:
        print("PASS: test 37 -> candidate 'Remote Global' + job 'Remote Global' -> MATCH")
    return failures


def test_38_bengaluru_plus_remote_global_job():
    failures = []
    result = assess_location_eligibility(_job("Remote Global"), _profile(["Bengaluru"]))
    if result.eligibility != LocationEligibility.NO_MATCH:
        _fail(failures, f"test 38: expected NO_MATCH, got {result.eligibility}")
    else:
        print("PASS: test 38 -> candidate 'Bengaluru' (no remote pref at all) + job 'Remote Global' -> NO_MATCH")
    return failures


def test_39_bengaluru_plus_remote_india_job():
    failures = []
    result = assess_location_eligibility(_job("Remote India"), _profile(["Bengaluru"]))
    if result.eligibility != LocationEligibility.NO_MATCH:
        _fail(failures, f"test 39: expected NO_MATCH, got {result.eligibility}")
    else:
        print("PASS: test 39 -> candidate 'Bengaluru' (no remote pref at all) + job 'Remote India' -> NO_MATCH")
    return failures


def test_40_bengaluru_plus_remote_global_pref_plus_remote_global_job():
    failures = []
    result = assess_location_eligibility(
        _job("Remote Global"), _profile(["Bengaluru", "Remote Global"])
    )
    if result.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"test 40: expected MATCH, got {result.eligibility}")
    else:
        print("PASS: test 40 -> candidate Bengaluru + explicit Remote Global pref + job Remote Global -> MATCH")
    return failures


def test_41_bengaluru_plus_remote_india_pref_plus_remote_global_job():
    """
    Mirrors the exact scenario flagged as too permissive: a candidate
    with a city preference plus an explicit but country-SCOPED remote
    preference (not global) must not automatically MATCH a global
    remote job.
    """
    failures = []
    result = assess_location_eligibility(
        _job("Remote Global"), _profile(["Bengaluru", "Remote India"])
    )
    if result.eligibility == LocationEligibility.MATCH:
        _fail(
            failures,
            "test 41: expected candidate with Bengaluru + Remote India "
            "(no explicit global preference) to NOT automatically MATCH "
            "a Remote Global job, but it did",
        )
    else:
        print(
            f"PASS: test 41 -> candidate Bengaluru + Remote India (no "
            f"global pref) + job Remote Global -> {result.eligibility.value} "
            f"(not auto-matched)"
        )
    return failures


def test_42_mixed_city_and_remote_preferences():
    """
    A candidate with both a city preference and a country-scoped remote
    preference should MATCH a job that satisfies either -- multi-entry
    preference lists must not collapse or lose either signal.
    """
    failures = []
    profile = _profile(["Bengaluru", "Remote India"])

    result_city_job = assess_location_eligibility(_job("Bengaluru"), profile)
    result_remote_job = assess_location_eligibility(_job("Remote India"), profile)
    result_other_city_job = assess_location_eligibility(_job("Pune"), profile)

    if result_city_job.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"test 42: expected city-pref pairing to MATCH a Bengaluru job, got {result_city_job.eligibility}")
    if result_remote_job.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"test 42: expected remote-pref pairing to MATCH a Remote India job, got {result_remote_job.eligibility}")
    if result_other_city_job.eligibility != LocationEligibility.NO_MATCH:
        _fail(failures, f"test 42: expected a Pune job to NO_MATCH a Bengaluru+RemoteIndia-only candidate, got {result_other_city_job.eligibility}")

    if not failures:
        print(
            "PASS: test 42 -> mixed city+remote candidate preferences "
            "each independently MATCH their corresponding job, without "
            "collapsing or interfering with each other"
        )

    return failures


# --------------------------------------------------------------------
# 43-52: India-coverage expansion (canonical cities, aliases, regions,
# multi-location preservation, unknown preservation, genericity, and a
# re-confirmation that the remote-scope matrix was not disturbed).
# --------------------------------------------------------------------

_CANONICAL_INDIA_CITIES = [
    "Bengaluru", "Hyderabad", "Pune", "Chennai", "Mumbai", "Delhi",
    "Noida", "Gurugram", "Kolkata", "Ahmedabad", "Kochi", "Bhubaneswar",
]


def test_43_all_canonical_india_cities():
    failures = []
    for city in _CANONICAL_INDIA_CITIES:
        loc = normalize_location_text(city)
        if loc.kind != LocationKind.CITY or loc.city != city or loc.country != "India":
            _fail(
                failures,
                f"test 43: {city!r} expected CITY {city!r}/India, got "
                f"kind={loc.kind} city={loc.city} country={loc.country}",
            )
    if not failures:
        print(f"PASS: test 43 -> all {len(_CANONICAL_INDIA_CITIES)} canonical India cities normalize to themselves, country=India")
    return failures


def test_44_requested_city_aliases():
    failures = []
    cases = [
        ("Bangalore", "Bengaluru"),
        ("Bangalore Urban", "Bengaluru"),
        ("Bombay", "Mumbai"),
        ("Gurgaon", "Gurugram"),
        ("New Delhi", "Delhi"),
        ("Greater Noida", "Noida"),
        ("Secunderabad", "Hyderabad"),
        ("Bhubaneshwar", "Bhubaneswar"),
        ("Cochin", "Kochi"),
    ]
    for alias, expected_city in cases:
        loc = normalize_location_text(alias)
        if loc.kind != LocationKind.CITY or loc.city != expected_city:
            _fail(
                failures,
                f"test 44: {alias!r} expected CITY {expected_city!r}, got "
                f"kind={loc.kind} city={loc.city}",
            )
    if not failures:
        print(f"PASS: test 44 -> all {len(cases)} requested aliases normalize to their canonical city")
    return failures


def test_45_delhi_ncr_variants():
    failures = []
    for variant in ["Delhi NCR", "NCR", "Delhi/NCR", "Delhi / NCR"]:
        loc = normalize_location_text(variant)
        if loc.kind != LocationKind.REGION or loc.region != "Delhi NCR":
            _fail(
                failures,
                f"test 45: {variant!r} expected REGION 'Delhi NCR', got "
                f"kind={loc.kind} region={loc.region}",
            )
    if not failures:
        print("PASS: test 45 -> 'Delhi NCR', 'NCR', 'Delhi/NCR', 'Delhi / NCR' all normalize to REGION/Delhi NCR")
    return failures


def test_46_national_india_and_pan_india():
    failures = []
    india = normalize_location_text("India")
    pan_india = normalize_location_text("PAN India")
    if india.kind != LocationKind.COUNTRY or india.country != "India":
        _fail(failures, f"test 46: 'India' expected COUNTRY/India, got kind={india.kind} country={india.country}")
    elif pan_india.kind != LocationKind.PAN_COUNTRY or pan_india.country != "India":
        _fail(failures, f"test 46: 'PAN India' expected PAN_COUNTRY/India, got kind={pan_india.kind} country={pan_india.country}")
    else:
        print("PASS: test 46 -> 'India' -> COUNTRY/India, 'PAN India' -> PAN_COUNTRY/India")
    return failures


def test_47_remote_variants_unchanged():
    failures = []
    remote = normalize_location_text("Remote")
    remote_india = normalize_location_text("Remote India")
    remote_global = normalize_location_text("Remote Global")
    if remote.kind != LocationKind.REMOTE_UNSPECIFIED:
        _fail(failures, f"test 47: 'Remote' expected REMOTE_UNSPECIFIED, got {remote.kind}")
    elif remote_india.kind != LocationKind.REMOTE_COUNTRY or remote_india.country != "India":
        _fail(failures, f"test 47: 'Remote India' expected REMOTE_COUNTRY/India, got kind={remote_india.kind} country={remote_india.country}")
    elif remote_global.kind != LocationKind.REMOTE_GLOBAL:
        _fail(failures, f"test 47: 'Remote Global' expected REMOTE_GLOBAL, got {remote_global.kind}")
    else:
        print("PASS: test 47 -> Remote/Remote India/Remote Global normalization unchanged by this expansion")
    return failures


def test_48_multi_location_preservation_new_cities():
    """
    "Mumbai/Pune" are two genuinely different cities and must be
    preserved as two distinct locations (not collapsed). This is
    distinct from "Noida/Greater Noida" and "Hyderabad/Secunderabad",
    where both halves are ALIASES of the same city -- those correctly
    produce two list entries that both resolve to the same canonical
    city, which is not a collapse bug: no information is lost, and
    eligibility matching (an OR across all entries) is unaffected
    either way.
    """
    failures = []

    mumbai_pune = parse_locations("Mumbai/Pune")
    cities = sorted(l.city for l in mumbai_pune if l.kind == LocationKind.CITY)
    if len(mumbai_pune) != 2 or cities != ["Mumbai", "Pune"]:
        _fail(
            failures,
            f"test 48: expected 'Mumbai/Pune' to preserve 2 distinct "
            f"cities [Mumbai, Pune], got {len(mumbai_pune)} entries: "
            f"{[(l.kind, l.city) for l in mumbai_pune]}",
        )
    else:
        print("PASS: test 48 -> 'Mumbai/Pune' preserved as 2 distinct CITY entries (not collapsed)")

    noida_variant = parse_locations("Noida/Greater Noida")
    if any(l.city != "Noida" for l in noida_variant):
        _fail(
            failures,
            f"test 48: expected 'Noida/Greater Noida' entries to both "
            f"resolve to canonical city Noida, got "
            f"{[(l.kind, l.city) for l in noida_variant]}",
        )

    hyd_variant = parse_locations("Hyderabad/Secunderabad")
    if any(l.city != "Hyderabad" for l in hyd_variant):
        _fail(
            failures,
            f"test 48: expected 'Hyderabad/Secunderabad' entries to both "
            f"resolve to canonical city Hyderabad, got "
            f"{[(l.kind, l.city) for l in hyd_variant]}",
        )

    if len(failures) <= 0:
        print("PASS: test 48 -> alias-pair multi-location strings ('Noida/Greater Noida', 'Hyderabad/Secunderabad') both resolve to one canonical city each, no information lost")

    return failures


def test_49_unknown_location_preservation():
    failures = []
    for unrecognized in ["Nagpur", "Lucknow", "Atlantis"]:
        loc = normalize_location_text(unrecognized)
        if loc.kind != LocationKind.UNKNOWN:
            _fail(
                failures,
                f"test 49: {unrecognized!r} is not in this taxonomy's "
                f"alias table and must remain UNKNOWN rather than being "
                f"guessed, got kind={loc.kind}",
            )
    if not failures:
        print("PASS: test 49 -> cities not covered by this expansion (Nagpur, Lucknow, Atlantis) remain UNKNOWN rather than guessed")
    return failures


def test_50_remote_scope_matrix_still_unchanged():
    """
    Re-confirms (using the exact cases from the prior remote-scope
    refinement) that this India-coverage expansion did not touch
    _remote_pair_eligibility() at all.
    """
    failures = []
    cases = [
        (["Remote"], "Remote India", LocationEligibility.MATCH),
        (["Remote"], "Remote Global", LocationEligibility.NO_MATCH),
        (["Remote India"], "Remote Global", LocationEligibility.UNKNOWN),
        (["Remote Global"], "Remote Global", LocationEligibility.MATCH),
    ]
    for cand_locs, job_location, expected in cases:
        result = assess_location_eligibility(_job(job_location), _profile(cand_locs))
        if result.eligibility != expected:
            _fail(
                failures,
                f"test 50: candidate {cand_locs} + job {job_location!r} "
                f"expected {expected}, got {result.eligibility}",
            )
    if not failures:
        print("PASS: test 50 -> remote-scope matrix from the prior refinement is unchanged")
    return failures


def test_51_new_cities_eligibility_is_generic():
    """
    Uses arbitrary, non-Saroj candidate profiles built from the newly
    added cities, proving eligibility over this expanded taxonomy is
    not specific to any one candidate's preferences.
    """
    failures = []

    result_match = assess_location_eligibility(_job("Mumbai"), _profile(["Mumbai"]))
    result_no_match = assess_location_eligibility(_job("Pune"), _profile(["Mumbai"]))
    result_alias_match = assess_location_eligibility(_job("Gurgaon"), _profile(["Gurugram"]))

    if result_match.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"test 51: candidate ['Mumbai'] + job 'Mumbai' expected MATCH, got {result_match.eligibility}")
    if result_no_match.eligibility != LocationEligibility.NO_MATCH:
        _fail(failures, f"test 51: candidate ['Mumbai'] + job 'Pune' expected NO_MATCH, got {result_no_match.eligibility}")
    if result_alias_match.eligibility != LocationEligibility.MATCH:
        _fail(
            failures,
            f"test 51: candidate ['Gurugram'] + job 'Gurgaon' (alias of "
            f"the same city) expected MATCH, got {result_alias_match.eligibility}",
        )

    if not failures:
        print("PASS: test 51 -> eligibility over the expanded city set works for an arbitrary non-Saroj candidate profile")

    return failures


def test_52_no_candidate_specific_hardcoding_still_holds():
    """
    Re-runs test 32's static check after this expansion -- the new
    aliases are generic taxonomy data (city name equivalences), not
    anyone's personal preferences, so this guarantee must still hold.
    """
    failures = test_32_no_candidate_specific_hardcoding()
    if not failures:
        print(
            "PASS: test 52 -> India-coverage expansion added only generic "
            "city-alias taxonomy data; no candidate-specific hardcoding "
            "was introduced"
        )
    return failures


def main():
    tests = [
        test_1_bangalore_normalizes_to_bengaluru,
        test_2_bengaluru_normalizes_to_bengaluru,
        test_3_hyderabad,
        test_4_pune,
        test_5_chennai,
        test_6_remote_india,
        test_7_remote_global,
        test_8_pan_india,
        test_9_delhi_ncr,
        test_10_multi_city_job,
        test_11_candidate_with_multiple_cities,
        test_12_candidate_bangalore_job_bengaluru,
        test_13_candidate_bangalore_job_hyderabad,
        test_14_candidate_beng_hyd_job_hyderabad,
        test_15_candidate_bengaluru_pan_india,
        test_16_candidate_bengaluru_remote_india_no_remote_pref,
        test_17_candidate_bengaluru_remote_india_with_remote_pref,
        test_18_candidate_bengaluru_remote_global_without_global_pref,
        test_19_candidate_with_global_remote_pref,
        test_20_unknown_job_location,
        test_21_unknown_candidate_location,
        test_22_hybrid_work_model,
        test_23_onsite_work_model,
        test_24_remote_work_model,
        test_25_work_model_unknown,
        test_26_multi_location_plus_hybrid,
        test_27_multi_location_plus_remote,
        test_28_different_candidates_different_eligibility,
        test_29_bangalore_bengaluru_normalization_equivalence,
        test_30_multi_location_preservation,
        test_31_unknown_preservation,
        test_32_no_candidate_specific_hardcoding,
        test_33_generic_remote_plus_remote_india_job,
        test_34_generic_remote_plus_remote_global_job,
        test_35_remote_india_plus_remote_india_job,
        test_36_remote_india_plus_remote_global_job,
        test_37_remote_global_plus_remote_global_job,
        test_38_bengaluru_plus_remote_global_job,
        test_39_bengaluru_plus_remote_india_job,
        test_40_bengaluru_plus_remote_global_pref_plus_remote_global_job,
        test_41_bengaluru_plus_remote_india_pref_plus_remote_global_job,
        test_42_mixed_city_and_remote_preferences,
        test_43_all_canonical_india_cities,
        test_44_requested_city_aliases,
        test_45_delhi_ncr_variants,
        test_46_national_india_and_pan_india,
        test_47_remote_variants_unchanged,
        test_48_multi_location_preservation_new_cities,
        test_49_unknown_location_preservation,
        test_50_remote_scope_matrix_still_unchanged,
        test_51_new_cities_eligibility_is_generic,
        test_52_no_candidate_specific_hardcoding_still_holds,
    ]

    all_failures = []

    for test in tests:
        all_failures.extend(test())

    print()

    if all_failures:
        print(f"{len(all_failures)} failure(s):")
        for failure in all_failures:
            print(f"  - {failure}")
        sys.exit(1)

    print(f"All {len(tests)} location taxonomy tests passed.")


if __name__ == "__main__":
    main()
