#!/usr/bin/env python3

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from discover_local import normalize_job
from canonical_job import normalize_title, normalize_company, derive_canonical_job
from location_taxonomy import WorkModel

FIXTURE_PATH = ROOT / "data" / "fixtures" / "canonical_jobs" / "cross_source_dataset.json"


def load_dataset():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def find_case(dataset, case_id):
    for case in dataset:
        if case["case_id"] == case_id:
            return case
    raise KeyError(f"case_id {case_id} not found in fixture")


def main():
    failures = []

    print("CANONICAL JOB NORMALIZATION TEST")
    print("=================================")

    # --- deterministic title normalization ---
    title_cases = [
        ("Senior Site Reliability Engineer", "senior site reliability engineer"),
        ("Sr. SRE", "senior site reliability engineer"),
        ("Senior SRE", "senior site reliability engineer"),
        ("Lead DevOps Engineer", "lead devops engineer"),
        ("Site Reliability Engineer III", "site reliability engineer 3"),
    ]
    for raw, expected in title_cases:
        got = normalize_title(raw)
        if got != expected:
            failures.append(f"normalize_title({raw!r}): expected {expected!r}, got {got!r}")
        else:
            print(f"PASS: normalize_title({raw!r}) -> {got!r}")

    # DevOps / SRE Engineer -- combined slash title, and must NOT collapse
    # into a form indistinguishable from a plain "Senior SRE" (different
    # role framing, no seniority word at all).
    combined = normalize_title("DevOps / SRE Engineer")
    if combined == normalize_title("Senior SRE"):
        failures.append(
            f"normalize_title('DevOps / SRE Engineer') incorrectly collapsed to the same "
            f"form as 'Senior SRE': {combined!r}"
        )
    elif "devops" not in combined or "engineer" not in combined:
        failures.append(f"normalize_title('DevOps / SRE Engineer') looks wrong: {combined!r}")
    else:
        print(f"PASS: normalize_title('DevOps / SRE Engineer') -> {combined!r} (distinct from Senior SRE)")

    # Determinism: same input always produces the same output.
    if normalize_title("Sr. SRE") != normalize_title("Sr. SRE"):
        failures.append("normalize_title is not deterministic")
    else:
        print("PASS: normalize_title is deterministic")

    # Must never silently merge different seniority levels.
    if normalize_title("Senior DevOps Engineer") == normalize_title("Lead DevOps Engineer"):
        failures.append(
            "SAFETY VIOLATION: normalize_title collapsed 'Senior' and 'Lead' into the same form"
        )
    else:
        print("PASS: 'Senior' and 'Lead' remain distinct after normalization")

    # --- deterministic company normalization ---
    company_cases = [
        ("Example Technologies", "example technologies"),
        ("Example Technologies Pvt Ltd", "example technologies"),
        ("Example Technologies Pvt. Ltd.", "example technologies"),
        ("Example Technologies Private Limited", "example technologies"),
    ]
    for raw, expected in company_cases:
        got = normalize_company(raw)
        if got != expected:
            failures.append(f"normalize_company({raw!r}): expected {expected!r}, got {got!r}")
        else:
            print(f"PASS: normalize_company({raw!r}) -> {got!r}")

    # Must never strip an ordinary business word.
    if normalize_company("Example Solutions Group") != "example solutions group":
        failures.append(
            "SAFETY VIOLATION: normalize_company stripped an ordinary business word "
            f"(got {normalize_company('Example Solutions Group')!r})"
        )
    else:
        print("PASS: ordinary business words are never stripped")

    dataset = load_dataset()

    # --- case 3: Bangalore/Bengaluru location normalization ---
    case3 = find_case(dataset, 3)
    job_a = derive_canonical_job(normalize_job(case3["jobs"][0], 1))
    job_b = derive_canonical_job(normalize_job(case3["jobs"][1], 2))
    if job_a.canonical_location != "Bengaluru" or job_b.canonical_location != "Bengaluru":
        failures.append(
            "case 3: expected both canonical_location == 'Bengaluru', got "
            f"{job_a.canonical_location!r} / {job_b.canonical_location!r}"
        )
    else:
        print(f"PASS: case 3 -> both canonical_location == 'Bengaluru' (raw: {case3['jobs'][0]['location']!r} / {case3['jobs'][1]['location']!r})")

    # --- case 10: missing experience -> unknown, not invented ---
    case10 = find_case(dataset, 10)
    job = derive_canonical_job(normalize_job(case10["jobs"][0], 1))
    if job.experience_min_years is not None or job.experience_max_years is not None:
        failures.append(
            f"case 10: expected experience_min/max_years both None, got "
            f"{job.experience_min_years!r} / {job.experience_max_years!r}"
        )
    else:
        print("PASS: case 10 -> missing experience_required yields experience_min/max_years = None")

    # --- case 11: missing salary -> always unknown (no source provides it) ---
    case11 = find_case(dataset, 11)
    job = derive_canonical_job(normalize_job(case11["jobs"][0], 1))
    if any(
        getattr(job, field) is not None
        for field in ("salary_min", "salary_max", "salary_currency", "salary_period", "employment_type", "updated_date")
    ):
        failures.append("case 11: expected salary/employment_type/updated_date all None (never invented)")
    else:
        print("PASS: case 11 -> salary/employment_type/updated_date remain None (never invented)")

    # --- case 12: remote job -> canonical work model REMOTE, location 'Remote' ---
    case12 = find_case(dataset, 12)
    job_a = derive_canonical_job(normalize_job(case12["jobs"][0], 1))
    job_b = derive_canonical_job(normalize_job(case12["jobs"][1], 2))
    if job_a.canonical_work_model != WorkModel.REMOTE.value:
        failures.append(f"case 12 job A: expected canonical_work_model REMOTE, got {job_a.canonical_work_model}")
    elif job_a.canonical_location != "Remote" or job_b.canonical_location != "Remote":
        failures.append(
            f"case 12: expected both canonical_location == 'Remote', got "
            f"{job_a.canonical_location!r} / {job_b.canonical_location!r}"
        )
    else:
        print("PASS: case 12 -> remote work model and canonical_location == 'Remote' preserved on both")

    # --- case 13: hybrid work model embedded in location text ---
    case13 = find_case(dataset, 13)
    job = derive_canonical_job(normalize_job(case13["jobs"][0], 1))
    if job.canonical_work_model != WorkModel.HYBRID.value:
        failures.append(f"case 13: expected canonical_work_model HYBRID, got {job.canonical_work_model}")
    elif job.canonical_location != "Bengaluru":
        failures.append(f"case 13: expected canonical_location 'Bengaluru', got {job.canonical_location!r}")
    else:
        print("PASS: case 13 -> HYBRID work model extracted, location still resolves to 'Bengaluru'")

    # --- case 14: unknown work model, never guessed ---
    case14 = find_case(dataset, 14)
    job = derive_canonical_job(normalize_job(case14["jobs"][0], 1))
    if job.canonical_work_model != WorkModel.UNKNOWN.value:
        failures.append(f"case 14: expected canonical_work_model UNKNOWN, got {job.canonical_work_model}")
    else:
        print("PASS: case 14 -> no work-model signal present -> UNKNOWN (never guessed)")

    # --- case 15: malformed/minimal listing must not crash ---
    case15 = find_case(dataset, 15)
    try:
        normalized = normalize_job(case15["jobs"][0], 1)
        job = derive_canonical_job(normalized)
        if job.description != "" or job.skills != [] or job.canonical_location is not None:
            failures.append(
                "case 15: expected empty description/skills and canonical_location=None for a "
                f"minimal listing, got description={job.description!r} skills={job.skills!r} "
                f"canonical_location={job.canonical_location!r}"
            )
        else:
            print("PASS: case 15 -> minimal listing normalizes without error, unknown fields stay unknown")
    except Exception as error:
        failures.append(f"case 15: derive_canonical_job raised on a minimal listing: {error!r}")

    # --- source_metadata never drops source-provided fields it has no
    #     dedicated canonical field for ---
    case1 = find_case(dataset, 1)
    normalized = normalize_job(case1["jobs"][0], 1)
    job = derive_canonical_job(normalized)
    if "discovered_at" not in job.source_metadata:
        failures.append("source_metadata: expected 'discovered_at' to be preserved from the normalized job dict")
    else:
        print("PASS: source_metadata preserves fields with no dedicated canonical column")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll canonical-job normalization tests passed.")


if __name__ == "__main__":
    main()
