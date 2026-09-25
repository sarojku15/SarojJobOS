#!/usr/bin/env python3

"""
Human review and confirmation CLI for a persisted CandidateProfile
DRAFT.

Pipeline this script sits in:

    candidate_profile.py
        v
    candidate_profile_store.py   (load the DRAFT JSON)
        v
    review_candidate_profile.py  (THIS FILE -- display, optional edits,
                                   revalidate)
        v
    candidate_profile.promote_to_confirmed()   (the ONLY promotion path)
        v
    candidate_profile_store.py   (save the CONFIRMED JSON to a NEW path)

No second profile schema is introduced here -- every field this CLI
displays or edits is a field candidate_profile.py already defines, and
every edit is applied directly onto the loaded CandidateProfile object
before handing it to the existing validate_candidate_profile() /
promote_to_confirmed() functions, unchanged.

============================================================================
CONFIRMATION SAFETY (the most important rule in this file)
============================================================================
A DRAFT never becomes CONFIRMED merely by being loaded, reviewed,
edited, or re-validated. Confirmation happens in exactly one place in
this file -- a single call to candidate_profile.promote_to_confirmed()
-- and only when the user passes --confirm explicitly. Nothing in this
file ever assigns profile.metadata.profile_status or
profile.metadata.confirmed_by_user directly; those are set only inside
promote_to_confirmed() itself.

The original DRAFT JSON file is only ever opened for reading
(candidate_profile_store.load_candidate_profile_draft()) -- this script
contains no code path that writes back to the input path. The confirmed
profile is written only to an explicitly-supplied --output path, which
must not already exist unless --force is also given.

No SQLite, no database of any kind, is imported or touched by this
file.

============================================================================
USAGE
============================================================================
Review only (no changes saved anywhere):
    python3 scripts/review_candidate_profile.py <draft_json>

Review with non-interactive edits (repeatable --set key=value):
    python3 scripts/review_candidate_profile.py <draft_json> \\
        --set name="Jane Doe" --set target_locations="Bengaluru,Remote India"

Review with interactive, prompt-driven edits:
    python3 scripts/review_candidate_profile.py <draft_json> --edit

Confirm (with or without edits) and save to a NEW path:
    python3 scripts/review_candidate_profile.py <draft_json> \\
        [--edit] [--set key=value ...] --confirm --output <confirmed_json> [--force]

Editable field keys (for --set and --edit):
    name, email, phone, headline, summary, relocation, salary_currency,
    experience, salary_min, salary_max,
    target_roles, target_locations, work_model,
    skills.<category> where <category> is one of:
        cloud, containers_orchestration, infrastructure_iac, cicd,
        observability, programming_scripting, databases_storage,
        security_iam, incident_itsm, other

List-valued fields (target_roles, target_locations, work_model, and
every skills.<category>) take a single comma-separated string.

If a field is left blank (Enter with no text in --edit, or simply not
passed via --set), its EXISTING value is preserved exactly -- nothing
here invents a replacement for missing information.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from candidate_profile import (
    Provenance,
    Skill,
    promote_to_confirmed,
    validate_candidate_profile,
)
from candidate_profile_store import (
    load_candidate_profile_draft,
    save_candidate_profile_draft,
)


SKILL_CATEGORIES = (
    "cloud",
    "containers_orchestration",
    "infrastructure_iac",
    "cicd",
    "observability",
    "programming_scripting",
    "databases_storage",
    "security_iam",
    "incident_itsm",
    "other",
)

# key -> (attribute holder, attribute name) for plain string fields.
_EDITABLE_SIMPLE_FIELDS = {
    "name": ("identity", "name"),
    "email": ("identity", "email"),
    "phone": ("identity", "phone"),
    "headline": ("professional_summary", "headline"),
    "summary": ("professional_summary", "summary"),
    "relocation": ("job_preferences", "relocation_preference"),
    "salary_currency": ("job_preferences", "salary_currency"),
}

# key -> (attribute holder, attribute name) for numeric fields.
_EDITABLE_NUMBER_FIELDS = {
    "experience": ("professional_summary", "total_experience_years"),
    "salary_min": ("job_preferences", "salary_expectation_min"),
    "salary_max": ("job_preferences", "salary_expectation_max"),
}

# key -> (attribute holder, attribute name) for comma-separated list fields.
_EDITABLE_LIST_FIELDS = {
    "target_roles": ("job_preferences", "target_roles"),
    "target_locations": ("job_preferences", "target_locations"),
    "work_model": ("job_preferences", "work_model_preferences"),
}


def apply_field_edit(profile, key, raw_value):
    """
    Apply a single edit identified by `key` to `profile` IN PLACE,
    using exactly `raw_value` -- never a derived or inferred value.
    Returns True if `key` was recognized, False otherwise. Raises
    ValueError if `key` names a numeric field and `raw_value` cannot be
    parsed as a number (callers should report this cleanly rather than
    letting it crash the whole review session).
    """
    if key in _EDITABLE_SIMPLE_FIELDS:
        section, attr = _EDITABLE_SIMPLE_FIELDS[key]
        setattr(getattr(profile, section), attr, raw_value or None)
        return True

    if key in _EDITABLE_NUMBER_FIELDS:
        section, attr = _EDITABLE_NUMBER_FIELDS[key]
        setattr(getattr(profile, section), attr, float(raw_value) if raw_value else None)
        return True

    if key in _EDITABLE_LIST_FIELDS:
        section, attr = _EDITABLE_LIST_FIELDS[key]
        values = [v.strip() for v in raw_value.split(",") if v.strip()]
        setattr(getattr(profile, section), attr, values)
        return True

    if key.startswith("skills."):
        category = key.split(".", 1)[1]
        if category not in SKILL_CATEGORIES:
            return False
        names = [v.strip() for v in raw_value.split(",") if v.strip()]
        setattr(
            profile.skills,
            category,
            [
                Skill(name=n, normalized_name=n.lower(), provenance=Provenance.USER_ENTERED)
                for n in names
            ],
        )
        return True

    return False


def _display(value):
    """Presentation-only: never derives a value, only formats an
    already-present (or already-absent) one."""
    if value in (None, "", [], {}):
        return "(not provided)"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "(not provided)"
    return str(value)


def print_review(profile, validation_result):
    print()
    print("=" * 72)
    print("CANDIDATE PROFILE REVIEW")
    print("=" * 72)

    print()
    print("1. IDENTITY")
    print(f"   Name  : {_display(profile.identity.name)}")
    print(f"   Email : {_display(profile.identity.email)}")
    print(f"   Phone : {_display(profile.identity.phone)}")

    print()
    print("2. PROFESSIONAL SUMMARY")
    print(f"   Headline   : {_display(profile.professional_summary.headline)}")
    print(f"   Title      : {_display(profile.professional_summary.current_title)}")
    print(f"   Summary    : {_display(profile.professional_summary.summary)}")
    print(f"   Experience : {_display(profile.professional_summary.total_experience_years)}")
    print(f"   Seniority  : {_display(profile.professional_summary.seniority_level)}")

    print()
    print("3. TARGET ROLES")
    print(f"   Target roles     : {_display(profile.job_preferences.target_roles)}")
    print(f"   Excluded roles   : {_display(profile.job_preferences.excluded_roles)}")
    print(f"   Target seniority : {_display(profile.job_preferences.target_seniority)}")

    print()
    print("4. SKILLS")
    any_skills = False
    for category in SKILL_CATEGORIES:
        skills = getattr(profile.skills, category)
        if skills:
            any_skills = True
            names = ", ".join(f"{s.name} ({s.normalized_name})" for s in skills)
            print(f"   {category:24}: {names}")
    if not any_skills:
        print("   (no skills recorded)")

    print()
    print("5. CERTIFICATIONS")
    if profile.certifications:
        for c in profile.certifications:
            suffix = f" -- {c.issuer}" if c.issuer else ""
            print(f"   - {c.name}{suffix}")
    else:
        print("   (none)")

    print()
    print("6. EDUCATION")
    if profile.education:
        for e in profile.education:
            line = e.institution
            if e.degree:
                line = f"{e.degree} -- {line}"
            if e.end_date:
                line = f"{line} ({e.end_date})"
            print(f"   - {line}")
    else:
        print("   (none)")

    print()
    print("7. EMPLOYMENT HISTORY")
    if profile.employment_history:
        for e in profile.employment_history:
            title = e.title or "(no title)"
            print(f"   - {title} at {e.employer} ({_display(e.start_date)} - {_display(e.end_date)})")
    else:
        print("   (none)")

    print()
    print("8. JOB PREFERENCES")
    print(f"   Target locations   : {_display(profile.job_preferences.target_locations)}")
    print(f"   Work model prefs   : {_display(profile.job_preferences.work_model_preferences)}")
    salary_min = profile.job_preferences.salary_expectation_min
    salary_max = profile.job_preferences.salary_expectation_max
    if salary_min is not None or salary_max is not None:
        currency = profile.job_preferences.salary_currency or ""
        salary_text = f"{_display(salary_min)} - {_display(salary_max)} {currency}".strip()
    else:
        salary_text = None
    print(f"   Salary expectation : {_display(salary_text)}")
    print(f"   Relocation pref    : {_display(profile.job_preferences.relocation_preference)}")

    print()
    print("9. METADATA")
    print(f"   Candidate ID    : {profile.identity.candidate_id}")
    print(f"   Profile status  : {profile.metadata.profile_status.value}")
    print(f"   Confirmed       : {profile.metadata.confirmed_by_user}")
    print(f"   Source          : {profile.metadata.source.value}")
    print(f"   Profile version : {profile.metadata.profile_version}")

    print()
    print("10. VALIDATION")
    if validation_result.error_issues:
        print("   ERRORS:")
        for issue in validation_result.error_issues:
            print(f"     - [{issue.field}] {issue.message}")
    else:
        print("   Errors  : none")

    if validation_result.warning_issues:
        print("   WARNINGS:")
        for issue in validation_result.warning_issues:
            print(f"     - [{issue.field}] {issue.message}")
    else:
        print("   Warnings: none")

    print()


def _ask(label, current_display):
    raw = input(f"   {label} [{current_display}] (Enter to keep): ")
    return raw.strip()


def run_interactive_edit(profile):
    print()
    print("EDIT MODE -- press Enter on any field to keep its current value.")
    print("Nothing is invented: a blank answer never changes a field.")
    print()

    for key, (section, attr) in _EDITABLE_SIMPLE_FIELDS.items():
        current = getattr(getattr(profile, section), attr)
        raw = _ask(key, _display(current))
        if raw:
            apply_field_edit(profile, key, raw)

    for key, (section, attr) in _EDITABLE_NUMBER_FIELDS.items():
        current = getattr(getattr(profile, section), attr)
        raw = _ask(key, _display(current))
        if raw:
            try:
                apply_field_edit(profile, key, raw)
            except ValueError:
                print(f"   Ignored -- '{raw}' is not a valid number for {key}.")

    for key, (section, attr) in _EDITABLE_LIST_FIELDS.items():
        current = getattr(getattr(profile, section), attr)
        raw = _ask(key, _display(current))
        if raw:
            apply_field_edit(profile, key, raw)

    for category in SKILL_CATEGORIES:
        current = getattr(profile.skills, category)
        current_names = ", ".join(s.name for s in current) if current else None
        raw = _ask(f"skills.{category}", _display(current_names))
        if raw:
            apply_field_edit(profile, f"skills.{category}", raw)


def _parse_args(argv):
    if not argv:
        return None

    draft_path = argv[0]
    rest = argv[1:]

    edit = False
    confirm = False
    force = False
    output = None
    sets = []

    i = 0
    while i < len(rest):
        token = rest[i]

        if token == "--edit":
            edit = True
            i += 1
        elif token == "--confirm":
            confirm = True
            i += 1
        elif token == "--force":
            force = True
            i += 1
        elif token == "--set":
            if i + 1 >= len(rest):
                print("ERROR: --set requires a key=value argument")
                sys.exit(1)
            sets.append(rest[i + 1])
            i += 2
        elif token == "--output":
            if i + 1 >= len(rest):
                print("ERROR: --output requires a path argument")
                sys.exit(1)
            output = rest[i + 1]
            i += 2
        else:
            print(f"ERROR: unrecognized argument: {token}")
            sys.exit(1)

    if confirm and not output:
        print("ERROR: --confirm requires --output <path>")
        sys.exit(1)

    return draft_path, edit, sets, confirm, output, force


def _print_usage():
    print("Usage:")
    print("  python3 scripts/review_candidate_profile.py <draft_json>")
    print("  python3 scripts/review_candidate_profile.py <draft_json> --edit")
    print("  python3 scripts/review_candidate_profile.py <draft_json> --set key=value [--set key=value ...]")
    print(
        "  python3 scripts/review_candidate_profile.py <draft_json> "
        "[--edit] [--set key=value ...] --confirm --output <confirmed_json> [--force]"
    )


def main():
    parsed = _parse_args(sys.argv[1:])

    if parsed is None:
        _print_usage()
        sys.exit(1)

    draft_path, edit, sets, confirm, output, force = parsed

    try:
        profile = load_candidate_profile_draft(draft_path)
    except ValueError as error:
        print(f"ERROR: {error}")
        sys.exit(1)

    loaded_status = profile.metadata.profile_status

    for item in sets:
        if "=" not in item:
            print(f"ERROR: --set value must be key=value, got {item!r}")
            sys.exit(1)

        key, _, value = item.partition("=")
        key = key.strip()

        try:
            recognized = apply_field_edit(profile, key, value.strip())
        except ValueError:
            print(f"ERROR: '{value}' is not a valid number for --set {key}")
            sys.exit(1)

        if not recognized:
            print(f"ERROR: unknown --set field {key!r}")
            sys.exit(1)

    if edit:
        run_interactive_edit(profile)

    validation_result = validate_candidate_profile(profile)
    print_review(profile, validation_result)

    if not confirm:
        print(
            f"Review complete. Profile status: {loaded_status.value}. "
            f"No changes were saved (pass --confirm --output <path> to confirm)."
        )
        print(f"Original draft file untouched: {draft_path}")
        return

    if not validation_result.valid:
        print(
            "ERROR: cannot confirm -- the profile has ERROR-severity "
            "validation issues (see VALIDATION above). Fix them with "
            "--edit or --set and try again."
        )
        sys.exit(1)

    output_path = Path(output)

    if output_path.exists() and not force:
        print(
            f"ERROR: output file already exists: {output_path} "
            f"(pass --force to overwrite it)"
        )
        sys.exit(1)

    try:
        confirmed_profile = promote_to_confirmed(profile)
    except ValueError as error:
        print(f"ERROR: {error}")
        sys.exit(1)

    save_candidate_profile_draft(confirmed_profile, output_path)

    print("PROFILE CONFIRMED")
    print("=================")
    print(f"  Candidate ID   : {confirmed_profile.identity.candidate_id}")
    print(f"  Profile status : {confirmed_profile.metadata.profile_status.value}")
    print(f"  Confirmed      : {confirmed_profile.metadata.confirmed_by_user}")
    print(f"  Saved to       : {output_path}")
    print()
    print(f"Original draft file untouched: {draft_path}")


if __name__ == "__main__":
    main()
